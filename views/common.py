"""Shared Streamlit helpers: the OpenF1 client, cached loaders and pickers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st

from pacelab import analysis, data, race
from pacelab.client import OpenF1Client, OpenF1Error, OpenF1LockedError
from pacelab.teams import driver_label
from pacelab.ui import driver_cell


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@st.cache_resource
def _client() -> OpenF1Client:
    return OpenF1Client.from_env()


def get_client() -> OpenF1Client:
    try:
        return _client()
    except OpenF1Error as exc:
        st.error(f"Could not connect to OpenF1: {exc}")
        st.stop()


@st.cache_data(ttl=3600, show_spinner=False)
def load_meetings(year: int) -> pd.DataFrame:
    return data.meetings(get_client(), year)


@st.cache_data(ttl=600, show_spinner=False)
def load_sessions(meeting_key: int) -> pd.DataFrame:
    return data.sessions(get_client(), meeting_key)


@st.cache_data(ttl=600, show_spinner=False)
def load_year_sessions(year: int, session_name: str) -> pd.DataFrame:
    df = pd.DataFrame(get_client().get("sessions", {"year": year, "session_name": session_name}, ttl=600))
    if not df.empty:
        for col in ("date_start", "date_end"):
            df[col] = pd.to_datetime(df[col], utc=True)
    return df


@st.cache_data(ttl=60, show_spinner="Loading laps from OpenF1…")
def load_laps(sessions_df: pd.DataFrame) -> pd.DataFrame:
    return data.load_sessions(get_client(), sessions_df)


@st.cache_data(ttl=60, show_spinner="Loading session from OpenF1…")
def load_race(session: dict[str, Any]) -> race.RaceData:
    return race.load_race(get_client(), session)


@st.cache_data(show_spinner="Drawing the track…")
def _outline(session: dict[str, Any], laps: pd.DataFrame) -> pd.DataFrame:
    return race.track_outline(get_client(), session, laps)


def load_outline(session: dict[str, Any], laps: pd.DataFrame) -> pd.DataFrame:
    try:
        return _outline(session, laps)
    except OpenF1LockedError:
        return pd.DataFrame(columns=["x", "y"])


@st.cache_data(ttl=600, show_spinner=False)
def practice_degradation(meeting_key: int) -> tuple[dict[str, float], str]:
    """Tyre degradation per compound from this weekend's practice long runs."""
    sessions = load_sessions(meeting_key)
    practice = sessions[
        (sessions["session_type"] == "Practice") & (sessions["date_end"] < pd.Timestamp(now_utc()))
    ]
    if practice.empty:
        return dict(race.DEFAULT_DEG), "typical values (no practice data)"
    try:
        laps = data.load_sessions(get_client(), practice)
    except OpenF1LockedError:
        return dict(race.DEFAULT_DEG), "typical values (practice data unavailable right now)"
    deg = analysis.compound_degradation(analysis.find_long_runs(laps))
    values = dict(race.DEFAULT_DEG)
    used = []
    for row in deg.itertuples():
        if row.runs >= 2 and 0 < row.deg_s_per_lap < 0.5:
            values[row.compound] = float(row.deg_s_per_lap)
            used.append(row.compound.title())
    source = f"this weekend's practice ({', '.join(used)})" if used else "typical values"
    return values, source


def meeting_picker(key: str, default_index: int | None = None) -> tuple[pd.Series, pd.DataFrame]:
    """Season and Grand Prix selectors in the sidebar."""
    now = now_utc()
    year = st.selectbox("Season", list(range(now.year, 2022, -1)), key=f"{key}_year")
    meetings_df = load_meetings(year)
    if meetings_df.empty:
        st.warning("No meetings found for this season.")
        st.stop()
    meetings_df = meetings_df[meetings_df["date_start"] <= pd.Timestamp(now)].reset_index(drop=True)
    if meetings_df.empty:
        st.info("This season has not started yet.")
        st.stop()
    if default_index is None or year != now.year:
        default_index = len(meetings_df) - 1
    idx = st.selectbox(
        "Grand Prix",
        meetings_df.index,
        index=int(min(default_index, len(meetings_df) - 1)),
        format_func=lambda i: f"{meetings_df.loc[i, 'meeting_name']} · {meetings_df.loc[i, 'date_start']:%d %b}",
        key=f"{key}_meeting_{year}",
    )
    meeting = meetings_df.loc[idx]
    return meeting, load_sessions(int(meeting["meeting_key"]))


def driver_picker(label: str, drivers: pd.DataFrame, default: list[int], key: str) -> list[int]:
    """Multiselect whose options show team colour, car number, driver and team.

    `drivers` needs driver_number, name_acronym, team_name and team_colour.
    """
    drivers = drivers.drop_duplicates("driver_number")
    labels = {
        int(r.driver_number): driver_label(r.driver_number, r.name_acronym, r.team_name, r.team_colour)
        for r in drivers.itertuples()
    }
    picked = st.multiselect(
        label,
        list(labels),
        default=[d for d in default if d in labels],
        format_func=lambda n: labels[n],
        key=key,
    )
    if picked:
        chips = drivers.set_index("driver_number").loc[picked]
        st.markdown(
            '<div class="pl-legend">'
            + "".join(
                f"<span>{driver_cell(n, r.name_acronym, r.team_name, r.team_colour)}</span>"
                for n, r in zip(picked, chips.itertuples())
            )
            + "</div>",
            unsafe_allow_html=True,
        )
    return picked


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)
