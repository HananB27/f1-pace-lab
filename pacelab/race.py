"""Race state engine: what a timing screen shows at any moment of a session.

The same code serves two sources:
- replay: a finished session loaded in full, viewed at any time `t`;
- live: a `LiveFeed` that polls OpenF1 during a session (sponsor tier).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from pacelab.analysis import SECTORS, sector_colour
from pacelab.client import OpenF1Client, OpenF1LockedError
from pacelab.data import enrich_laps, session_ttl

ENDPOINTS = ("drivers", "laps", "stints", "pit", "position", "intervals", "race_control")
DEFAULT_PIT_LOSS_S = 22.0
# Fallback degradation (s/lap) when practice data gives nothing usable.
DEFAULT_DEG = {"SOFT": 0.10, "MEDIUM": 0.07, "HARD": 0.05}


def to_ts(values: Any) -> Any:
    return pd.to_datetime(values, utc=True, format="ISO8601")


def iso(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.%f")


@dataclass
class RaceData:
    session: dict[str, Any]
    drivers: pd.DataFrame
    laps: pd.DataFrame
    stints: pd.DataFrame
    pits: pd.DataFrame
    positions: pd.DataFrame
    intervals: pd.DataFrame
    race_control: pd.DataFrame
    sectors: pd.DataFrame
    t_start: pd.Timestamp | None
    t_end: pd.Timestamp | None
    finished: bool

    @property
    def total_laps(self) -> int | None:
        if self.finished and not self.laps.empty:
            return int(self.laps["lap_number"].max())
        return None

    def lap_start_time(self, lap: int) -> pd.Timestamp | None:
        """When the leader started a given lap (for 'jump to lap')."""
        starts = self.laps.loc[self.laps["lap_number"] == lap, "date_start"].dropna()
        return starts.min() if not starts.empty else None


def _frame(rows: list[dict[str, Any]], date_cols: tuple[str, ...] = ("date",)) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in date_cols:
        if col in df.columns:
            df[col] = to_ts(df[col])
    if "date" in df.columns:
        df = df.sort_values("date", kind="stable").reset_index(drop=True)
    return df


def prepare_drivers(drivers: pd.DataFrame) -> pd.DataFrame:
    cols = ["driver_number", "name_acronym", "full_name", "team_name", "team_colour", "headshot_url"]
    if drivers.empty:
        return pd.DataFrame(columns=cols)
    df = drivers.drop_duplicates("driver_number", keep="last").copy()
    for col in cols:
        if col not in df.columns:
            df[col] = None
    df["team_colour"] = "#" + df["team_colour"].astype(object).fillna("888888").astype(str)
    df["name_acronym"] = df["name_acronym"].fillna(df["driver_number"].astype(str))
    return df[cols].reset_index(drop=True)


def sector_events(laps: pd.DataFrame) -> pd.DataFrame:
    """One row per completed sector, with the moment it was completed."""
    cols = ["driver_number", "lap_number", "sector", "duration", "end"]
    if laps.empty:
        return pd.DataFrame(columns=cols)
    parts = []
    elapsed = pd.Series(0.0, index=laps.index)
    for number, col in enumerate(SECTORS, start=1):
        duration = pd.to_numeric(laps[col], errors="coerce")
        elapsed = elapsed + duration
        end = laps["date_start"] + pd.to_timedelta(elapsed, unit="s")
        parts.append(
            pd.DataFrame(
                {
                    "driver_number": laps["driver_number"],
                    "lap_number": laps["lap_number"],
                    "sector": number,
                    "duration": duration,
                    "end": end,
                }
            )
        )
    out = pd.concat(parts, ignore_index=True).dropna(subset=["duration", "end"])
    return out.sort_values("end").reset_index(drop=True)


def build_race_data(session: dict[str, Any], raw: dict[str, list[dict[str, Any]]]) -> RaceData:
    drivers_raw = pd.DataFrame(raw.get("drivers") or [])
    laps_raw = pd.DataFrame(raw.get("laps") or [])
    stints = pd.DataFrame(raw.get("stints") or [])
    pits = _frame(raw.get("pit") or [])
    positions = _frame(raw.get("position") or [])
    intervals = _frame(raw.get("intervals") or [])
    race_control = _frame(raw.get("race_control") or [])

    laps = enrich_laps(laps_raw, stints, pits, drivers_raw)
    if not laps.empty:
        laps = laps.sort_values(["driver_number", "lap_number"]).reset_index(drop=True)
        own_end = laps["date_start"] + pd.to_timedelta(laps["lap_duration"], unit="s")
        next_start = laps.groupby("driver_number")["date_start"].shift(-1)
        laps["lap_end"] = own_end.fillna(next_start)
    else:
        laps = pd.DataFrame(columns=["driver_number", "lap_number", "date_start", "lap_end", "lap_duration", "compound", "tyre_age", *SECTORS])

    t_start = None
    if not laps.empty and laps["date_start"].notna().any():
        t_start = laps.loc[laps["lap_number"] == laps["lap_number"].min(), "date_start"].min()
    elif not positions.empty:
        t_start = positions["date"].min()
    ends = [s for s in (laps["lap_end"].max() if not laps.empty else None,
                        positions["date"].max() if not positions.empty else None) if s is not None and pd.notna(s)]
    t_end = max(ends) if ends else None

    return RaceData(
        session=session,
        drivers=prepare_drivers(drivers_raw),
        laps=laps,
        stints=stints,
        pits=pits,
        positions=positions,
        intervals=intervals,
        race_control=race_control,
        sectors=sector_events(laps),
        t_start=t_start,
        t_end=t_end,
        finished=session_ttl(session) is None,
    )


def load_race(client: OpenF1Client, session: dict[str, Any]) -> RaceData:
    """Load a whole session (replay). Cached forever once the session has settled."""
    key = {"session_key": session["session_key"]}
    ttl = session_ttl(session)
    raw = {ep: client.get(ep, key, ttl=ttl) for ep in ENDPOINTS}
    return build_race_data(session, raw)


# ------------------------------------------------------------------ state at t

def _latest(df: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["date"] <= t].groupby("driver_number").tail(1).set_index("driver_number")


sector_status = sector_colour


def track_status(race_control: pd.DataFrame, t: pd.Timestamp, leader_lap: int | None = None) -> str:
    status = "GREEN"
    ending_lap: Any = None
    ending_at = t
    if race_control.empty:
        return status
    for row in race_control[race_control["date"] <= t].itertuples():
        message = str(getattr(row, "message", "")).upper()
        category = str(getattr(row, "category", ""))
        flag = str(getattr(row, "flag", "")).upper()
        scope = str(getattr(row, "scope", ""))
        if category == "SafetyCar":
            if "DEPLOYED" in message:
                status = "VSC" if "VIRTUAL" in message else "SC"
            elif "ENDING" in message or "IN THIS LAP" in message:
                status = "VSC ENDING" if status.startswith("VSC") else "SC ENDING"
                ending_lap = getattr(row, "lap_number", None)
                ending_at = row.date
        elif flag == "RED":
            status = "RED"
        elif flag == "CHEQUERED":
            status = "CHEQUERED"
        elif flag == "GREEN" and scope == "Track":
            status = "GREEN"
    # Race control rarely announces the restart itself: the safety car comes in at
    # the end of the announced lap, and a VSC ends a few seconds after "ENDING".
    if status == "SC ENDING" and leader_lap is not None and pd.notna(ending_lap) and leader_lap > ending_lap:
        status = "GREEN"
    elif status == "VSC ENDING" and t - ending_at > pd.Timedelta(seconds=20):
        status = "GREEN"
    return status


@dataclass
class RaceState:
    t: pd.Timestamp
    tower: pd.DataFrame
    leader_lap: int
    total_laps: int | None
    status: str
    messages: pd.DataFrame
    fastest_lap: dict[str, Any] | None
    best_sectors: dict[int, dict[str, Any]] = field(default_factory=dict)


def state_at(data: RaceData, t: pd.Timestamp) -> RaceState:
    drivers = data.drivers.set_index("driver_number")
    tower = drivers[["name_acronym", "full_name", "team_name", "team_colour"]].copy()

    pos = _latest(data.positions, t)
    if pos.empty and not data.positions.empty:
        pos = data.positions.groupby("driver_number").head(1).set_index("driver_number")
    tower["position"] = pos["position"] if not pos.empty else np.nan

    iv = _latest(data.intervals, t)
    tower["gap"] = iv["gap_to_leader"] if not iv.empty else None
    tower["interval"] = iv["interval"] if not iv.empty else None

    laps = data.laps
    started = laps[laps["date_start"] <= t]
    done = laps[laps["lap_end"] <= t]
    current = started.sort_values("lap_number").groupby("driver_number").tail(1).set_index("driver_number")
    last = done.sort_values("lap_number").groupby("driver_number").tail(1).set_index("driver_number")
    tower["lap"] = current["lap_number"] if not current.empty else np.nan
    tower["last_lap"] = last["lap_duration"] if not last.empty else np.nan
    tower["best_lap"] = done.groupby("driver_number")["lap_duration"].min() if not done.empty else np.nan
    tower["compound"] = current["compound"] if not current.empty else None
    tower["tyre_age"] = current["tyre_age"] if not current.empty else np.nan

    # Before the start nobody has a lap yet: show the tyre they start on.
    if not data.stints.empty:
        first = data.stints.sort_values("stint_number").groupby("driver_number").head(1).set_index("driver_number")
        tower["compound"] = tower["compound"].fillna(first["compound"])
        tower["tyre_age"] = tower["tyre_age"].fillna(first["tyre_age_at_start"])

    # Pit stops during the session so far, and who is in the pit lane right now.
    tower["pits"] = 0
    tower["in_pit"] = False
    pits = data.pits
    if not pits.empty and data.t_start is not None:
        pits = pits[(pits["date"] >= data.t_start) & (pits["date"] <= t)]
        if not pits.empty:
            tower["pits"] = pits.groupby("driver_number").size().reindex(tower.index).fillna(0).astype(int)
            lane = pd.to_numeric(pits["lane_duration"], errors="coerce").fillna(25.0) if "lane_duration" in pits.columns else pd.Series(25.0, index=pits.index)
            until = pits["date"] + pd.to_timedelta(lane, unit="s")
            tower["in_pit"] = tower.index.isin(pits.loc[until >= t, "driver_number"])

    # Sectors: show the latest lap that has at least one completed sector.
    events = data.sectors[data.sectors["end"] <= t]
    best_sectors: dict[int, dict[str, Any]] = {}
    for n in (1, 2, 3):
        tower[f"s{n}"] = np.nan
        tower[f"s{n}_status"] = ""
    if not events.empty:
        overall = events.groupby("sector")["duration"].min()
        personal = events.groupby(["driver_number", "sector"])["duration"].min()
        latest_lap = events.groupby("driver_number")["lap_number"].max()
        recent = events[events["lap_number"] == events["driver_number"].map(latest_lap)]
        for row in recent.itertuples():
            if row.driver_number not in tower.index:
                continue
            tower.loc[row.driver_number, f"s{row.sector}"] = row.duration
            tower.loc[row.driver_number, f"s{row.sector}_status"] = sector_status(
                row.duration, personal.get((row.driver_number, row.sector)), overall.get(row.sector)
            )
        for sector, value in overall.items():
            holder = events[(events["sector"] == sector) & np.isclose(events["duration"], value)].iloc[0]
            best_sectors[int(sector)] = {"time": float(value), "driver_number": int(holder["driver_number"])}

    leader_lap = int(tower["lap"].max()) if tower["lap"].notna().any() else 0

    # Retired: no completed lap for 4+ minutes while the race is still running.
    tower["out"] = False
    if not done.empty and data.t_end is not None and t < data.t_end:
        last_end = done.groupby("driver_number")["lap_end"].max().reindex(tower.index)
        stale = (t - last_end) > pd.Timedelta(minutes=4)
        tower["out"] = stale.fillna(False) & (tower["lap"].fillna(0) < leader_lap)

    tower = tower.reset_index().sort_values(["position", "driver_number"], na_position="last")

    fastest = None
    if not done.empty and done["lap_duration"].notna().any():
        row = done.loc[done["lap_duration"].idxmin()]
        fastest = {"driver_number": int(row["driver_number"]), "time": float(row["lap_duration"]), "lap": int(row["lap_number"])}

    messages = data.race_control
    if not messages.empty:
        messages = messages[messages["date"] <= t].tail(8).iloc[::-1]

    return RaceState(
        t=t,
        tower=tower.reset_index(drop=True),
        leader_lap=leader_lap,
        total_laps=data.total_laps,
        status=track_status(data.race_control, t, leader_lap),
        messages=messages,
        fastest_lap=fastest,
        best_sectors=best_sectors,
    )


# ------------------------------------------------------------------ strategy

def pit_loss(data: RaceData, t: pd.Timestamp) -> float:
    """Median pit-lane time loss in this session so far, or a typical default."""
    pits = data.pits
    if pits.empty or "lane_duration" not in pits.columns:
        return DEFAULT_PIT_LOSS_S
    lane = pd.to_numeric(pits.loc[pits["date"] <= t, "lane_duration"], errors="coerce")
    lane = lane[(lane > 10) & (lane < 60)]
    return float(lane.median()) if len(lane) >= 2 else DEFAULT_PIT_LOSS_S


def add_pit_model(
    tower: pd.DataFrame, deg: dict[str, float], remaining_laps: int | None, loss: float
) -> pd.DataFrame:
    """Rough 'should I pit now?' numbers from a linear tyre model.

    With linear degradation d, a tyre that is `age` laps old is d*age slower per lap
    than a new one, for every remaining lap. Pitting now instead of never gains
    d*age*remaining - pit_loss seconds.
    """
    out = tower.copy()
    rate = out["compound"].map(deg).astype(float).fillna(np.mean(list(DEFAULT_DEG.values())))
    out["tyre_loss"] = rate * pd.to_numeric(out["tyre_age"], errors="coerce")
    if remaining_laps and remaining_laps > 0:
        out["pit_gain"] = out["tyre_loss"] * remaining_laps - loss
    else:
        out["pit_gain"] = np.nan
    return out


# ------------------------------------------------------------------ car positions

class LocationStore:
    """Car positions for replay, fetched in fixed time chunks and kept in memory."""

    def __init__(self, client: OpenF1Client, session_key: int, cache: bool = True, chunk_s: int = 120):
        self.client = client
        self.session_key = session_key
        self.cache = cache
        self.chunk_s = chunk_s
        self.chunks: dict[int, pd.DataFrame] = {}
        self.blocked_until = 0.0

    def _chunk(self, idx: int) -> pd.DataFrame:
        if idx not in self.chunks:
            start = pd.Timestamp(idx * self.chunk_s, unit="s", tz="UTC")
            rows = self.client.get(
                "location",
                {
                    "session_key": self.session_key,
                    "date>=": iso(start),
                    "date<": iso(start + pd.Timedelta(seconds=self.chunk_s)),
                },
                ttl=None if self.cache else 0,
            )
            df = _frame(rows)
            if not df.empty:
                df = df[["driver_number", "date", "x", "y"]]
                df = df[(df["x"] != 0) | (df["y"] != 0)]
            self.chunks[idx] = df
            for old in sorted(self.chunks)[:-4]:
                del self.chunks[old]
        return self.chunks[idx]

    def _chunks(self, indices: list[int]) -> list[pd.DataFrame]:
        frames = []
        for i in indices:
            if i not in self.chunks and time.monotonic() < self.blocked_until:
                continue
            try:
                frames.append(self._chunk(i))
            except OpenF1LockedError:
                # Not cached and the API is closed right now: show no cars and
                # wait a minute before asking again, so replay keeps running.
                self.blocked_until = time.monotonic() + 60
        return frames

    def window(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Every position sample between two moments (about 3.7 per car per second)."""
        first, last = int(start.timestamp() // self.chunk_s), int(end.timestamp() // self.chunk_s)
        frames = [f for f in self._chunks(list(range(first, last + 1))) if not f.empty]
        if not frames:
            return pd.DataFrame(columns=["driver_number", "date", "x", "y"])
        df = pd.concat(frames, ignore_index=True)
        return df[(df["date"] >= start) & (df["date"] <= end)]

    def at(self, t: pd.Timestamp, max_age_s: float = 8.0) -> pd.DataFrame:
        samples = self.window(t - pd.Timedelta(seconds=max_age_s), t)
        return latest_positions(samples, t, max_age_s)


def latest_positions(df: pd.DataFrame, t: pd.Timestamp, max_age_s: float = 8.0) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["driver_number", "x", "y"])
    df = df[(df["date"] <= t) & (df["date"] >= t - pd.Timedelta(seconds=max_age_s))]
    return df.sort_values("date").groupby("driver_number").tail(1)[["driver_number", "x", "y"]]


def track_outline(client: OpenF1Client, session: dict[str, Any], laps: pd.DataFrame) -> pd.DataFrame:
    """Track shape from one clean, fast lap of car positions."""
    if laps.empty:
        return pd.DataFrame(columns=["x", "y"])
    clean = laps[laps["lap_duration"].notna() & ~laps["is_pit_out_lap"].astype(bool) & ~laps["is_in_lap"].astype(bool)]
    if clean.empty:
        return pd.DataFrame(columns=["x", "y"])
    # A lap near the fast end but not the very fastest, which can be a timing glitch.
    ordered = clean.sort_values("lap_duration")
    lap = ordered.iloc[min(len(ordered) - 1, 2)]
    start = lap["date_start"]
    rows = client.get(
        "location",
        {
            "session_key": session["session_key"],
            "driver_number": int(lap["driver_number"]),
            "date>=": iso(start),
            "date<": iso(start + pd.Timedelta(seconds=float(lap["lap_duration"]))),
        },
        ttl=None,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["x", "y"])
    df = df[(df["x"] != 0) | (df["y"] != 0)]
    outline = df[["x", "y"]].reset_index(drop=True)
    # Close the loop.
    return pd.concat([outline, outline.iloc[:1]], ignore_index=True)


# ------------------------------------------------------------------ live feed

class LiveFeed:
    """Polls OpenF1 during a session and accumulates everything seen so far.

    Request budget per poll (every POLL_S = 3 seconds):
    - car locations, incrementally, every poll           -> 20 requests/minute
    - positions and intervals, every `fast_every` polls  -> 20 requests/minute
    - laps, stints, pit stops, race control, every `slow_every` polls -> 8/minute
    That is about 48 requests/minute, inside the sponsor tier's 60.

    Car locations are kept for the last `history_s` seconds so the browser can
    animate them smoothly a few seconds behind real time.

    `until` simulates live mode on a finished session by capping the time;
    it is used for testing.
    """

    POLL_S = 3
    FAST = ("position", "intervals")
    SLOW = ("laps", "stints", "pit", "race_control")

    def __init__(
        self,
        client: OpenF1Client,
        session: dict[str, Any],
        until: Callable[[], pd.Timestamp] | None = None,
        fast_every: int = 2,
        slow_every: int = 10,
        history_s: float = 90,
    ) -> None:
        self.client = client
        self.session = session
        self.until = until
        self.fast_every = fast_every
        self.slow_every = slow_every
        self.history = pd.Timedelta(seconds=history_s)
        self.raw: dict[str, list[dict[str, Any]]] = {ep: [] for ep in ENDPOINTS}
        self.last_date: dict[str, str] = {}
        self.last_location: str | None = None
        self.locations = pd.DataFrame(columns=["driver_number", "date", "x", "y"])
        self.polls = 0

    def _get(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self.client.get(endpoint, {"session_key": self.session["session_key"], **params}, cache=False)

    def _cap(self) -> dict[str, str]:
        return {"date<=": iso(self.until())} if self.until else {}

    def poll(self) -> None:
        self.polls += 1
        first = self.polls == 1
        if not self.raw["drivers"]:
            self.raw["drivers"] = self._get("drivers", {})

        if first or self.polls % self.fast_every == 0:
            for ep in self.FAST:
                params = dict(self._cap())
                if ep in self.last_date:
                    params["date>"] = self.last_date[ep]
                rows = self._get(ep, params)
                if rows:
                    self.raw[ep].extend(rows)
                    self.last_date[ep] = max(r["date"] for r in rows)

        self._poll_locations()

        if first or self.polls % self.slow_every == 0:
            for ep in self.SLOW:
                self.raw[ep] = self._get(ep, {})

    def _poll_locations(self) -> None:
        params = dict(self._cap())
        if self.last_location:
            params["date>"] = self.last_location
        else:
            newest = self.data_time()
            if newest is None:
                return
            params["date>"] = iso(newest - pd.Timedelta(seconds=10))
        rows = self._get("location", params)
        if not rows:
            return
        self.last_location = max(r["date"] for r in rows)
        fresh = _frame(rows)[["driver_number", "date", "x", "y"]]
        fresh = fresh[(fresh["x"] != 0) | (fresh["y"] != 0)]
        merged = pd.concat([self.locations, fresh], ignore_index=True)
        self.locations = merged[merged["date"] >= merged["date"].max() - self.history]

    def data_time(self) -> pd.Timestamp | None:
        dates = [self.last_date[ep] for ep in self.FAST if ep in self.last_date]
        return max(to_ts(pd.Series(dates))) if dates else None

    def location_time(self) -> pd.Timestamp | None:
        return to_ts(self.last_location) if self.last_location else None

    def location_window(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        df = self.locations
        if df.empty:
            return df
        return df[(df["date"] >= start) & (df["date"] <= end)]

    def data(self) -> RaceData:
        return build_race_data(self.session, self.raw)
