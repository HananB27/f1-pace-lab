"""Load OpenF1 data for a race weekend into tidy pandas DataFrames."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from pacelab.client import OpenF1Client

# Session data keeps being corrected for a while after the chequered flag.
SETTLE_TIME = timedelta(hours=2)
LIVE_TTL_S = 60


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).to_pydatetime()


def session_ttl(session: dict[str, Any], now: datetime | None = None) -> float | None:
    """Cache forever once a session has settled, otherwise refresh every minute."""
    now = now or datetime.now(timezone.utc)
    end = _parse_time(session.get("date_end"))
    if end is not None and now > end + SETTLE_TIME:
        return None
    return LIVE_TTL_S


def meetings(client: OpenF1Client, year: int) -> pd.DataFrame:
    df = pd.DataFrame(client.get("meetings", {"year": year}, ttl=6 * 3600))
    if df.empty:
        return df
    df["date_start"] = pd.to_datetime(df["date_start"], utc=True)
    return df.sort_values("date_start").reset_index(drop=True)


def sessions(client: OpenF1Client, meeting_key: int) -> pd.DataFrame:
    df = pd.DataFrame(client.get("sessions", {"meeting_key": meeting_key}, ttl=3600))
    if df.empty:
        return df
    for col in ("date_start", "date_end"):
        df[col] = pd.to_datetime(df[col], utc=True)
    return df.sort_values("date_start").reset_index(drop=True)


def current_meeting_index(meetings_df: pd.DataFrame, now: datetime | None = None) -> int:
    """Index of the latest meeting that has already started (the 'current' weekend)."""
    now = now or datetime.now(timezone.utc)
    started = meetings_df.index[meetings_df["date_start"] <= pd.Timestamp(now)]
    return int(started[-1]) if len(started) else 0


def load_session(client: OpenF1Client, session: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Fetch laps, stints, pit stops and drivers for one session and join them."""
    key = {"session_key": session["session_key"]}
    ttl = session_ttl(session)
    laps = pd.DataFrame(client.get("laps", key, ttl=ttl))
    stints = pd.DataFrame(client.get("stints", key, ttl=ttl))
    pits = pd.DataFrame(client.get("pit", key, ttl=ttl))
    drivers = pd.DataFrame(client.get("drivers", key, ttl=ttl))

    enriched = enrich_laps(laps, stints, pits, drivers)
    enriched["session_key"] = session["session_key"]
    enriched["session_name"] = session.get("session_name", str(session["session_key"]))
    return {"laps": enriched, "drivers": drivers}


def load_sessions(client: OpenF1Client, sessions_df: pd.DataFrame) -> pd.DataFrame:
    frames = [load_session(client, row)["laps"] for row in sessions_df.to_dict("records")]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


LAP_COLUMNS = [
    "driver_number", "lap_number", "date_start", "lap_duration",
    "duration_sector_1", "duration_sector_2", "duration_sector_3",
    "i1_speed", "i2_speed", "st_speed", "is_pit_out_lap",
]


def enrich_laps(
    laps: pd.DataFrame,
    stints: pd.DataFrame,
    pits: pd.DataFrame,
    drivers: pd.DataFrame,
) -> pd.DataFrame:
    """Attach tyre compound/age, in-lap flag and driver/team info to each lap."""
    if laps.empty:
        return pd.DataFrame(columns=LAP_COLUMNS)

    df = laps[[c for c in LAP_COLUMNS if c in laps.columns]].copy()
    df["is_pit_out_lap"] = df["is_pit_out_lap"].fillna(False).astype(bool)
    df["date_start"] = pd.to_datetime(df["date_start"], utc=True, format="ISO8601")

    # Tyre info: each stint covers lap_start..lap_end for one driver.
    df["stint_number"] = pd.NA
    df["compound"] = "UNKNOWN"
    df["tyre_age"] = pd.NA
    if not stints.empty:
        for stint in stints.to_dict("records"):
            start, end = stint.get("lap_start"), stint.get("lap_end")
            if pd.isna(start) or pd.isna(end):
                continue
            mask = (
                (df["driver_number"] == stint["driver_number"])
                & (df["lap_number"] >= start)
                & (df["lap_number"] <= end)
            )
            df.loc[mask, "stint_number"] = stint["stint_number"]
            compound = stint.get("compound")
            df.loc[mask, "compound"] = compound if isinstance(compound, str) else "UNKNOWN"
            age0 = stint.get("tyre_age_at_start")
            age0 = 0 if pd.isna(age0) else age0
            df.loc[mask, "tyre_age"] = age0 + df.loc[mask, "lap_number"] - start

    # In-laps: OpenF1 records a pit entry against the lap it happened on.
    in_laps: set[tuple[int, int]] = set()
    if not pits.empty and "lap_number" in pits.columns:
        in_laps = {
            (int(r.driver_number), int(r.lap_number))
            for r in pits.itertuples()
            if pd.notna(r.lap_number)
        }
    df["is_in_lap"] = [
        (int(d), int(n)) in in_laps for d, n in zip(df["driver_number"], df["lap_number"])
    ]

    if not drivers.empty:
        info = drivers[["driver_number", "name_acronym", "full_name", "team_name", "team_colour"]]
        info = info.drop_duplicates("driver_number")
        df = df.merge(info, on="driver_number", how="left")
    for col in ("name_acronym", "full_name", "team_name"):
        if col not in df.columns:
            df[col] = None
    df["name_acronym"] = df["name_acronym"].fillna(df["driver_number"].astype(str))
    df["team_name"] = df["team_name"].fillna("Unknown")
    colour = df["team_colour"] if "team_colour" in df.columns else pd.Series(None, index=df.index)
    df["team_colour"] = "#" + colour.astype(object).fillna("888888").astype(str)

    df["stint_number"] = pd.to_numeric(df["stint_number"], errors="coerce")
    df["tyre_age"] = pd.to_numeric(df["tyre_age"], errors="coerce")
    return df.sort_values(["driver_number", "lap_number"]).reset_index(drop=True)
