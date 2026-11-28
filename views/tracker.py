"""Race tracker: timing tower and live track map, as a replay or live (sponsor tier)."""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
import streamlit as st

from pacelab import race
from pacelab.client import OpenF1LockedError
from pacelab.ui import (
    cards,
    driver_cell,
    flag_banner,
    gap_text,
    laptime,
    messages_html,
    sector_cell,
    sector_legend,
    table,
    tyre_icon,
)
from views.track_map import track_map
from views.common import (
    get_client,
    html,
    load_outline,
    load_race,
    load_sessions,
    load_year_sessions,
    meeting_picker,
    now_utc,
    load_meetings,
    practice_degradation,
)

SPEEDS = [1, 2, 5, 10, 30]
LIVE_POLL_S = race.LiveFeed.POLL_S
LIVE_DELAY_S = 5  # draw live cars slightly behind real time so the buffer never runs dry
RACE_TYPES = {"Race", "Sprint"}

client = get_client()
now = now_utc()


# ---------------------------------------------------------------- rendering

def tower_html(state: race.RaceState, is_race: bool, show_model: bool) -> str:
    fastest = state.fastest_lap["time"] if state.fastest_lap else None
    headers = [("Pos", "pos"), ("Driver", "")]
    if is_race:
        headers += [("Gap", "num"), ("Int", "num")]
    headers += [("Tyre · stops", ""), ("Last lap", "num"), ("S1", "num"), ("S2", "num"), ("S3", "num"), ("Best", "num")]
    if show_model:
        headers += [("Pit now?", "num")]

    rows = []
    for r in state.tower.itertuples():
        if pd.isna(r.position):
            continue
        tag = ""
        if r.out:
            tag = '<span class="pl-tag out">OUT</span>'
        elif r.in_pit:
            tag = '<span class="pl-tag pit">PIT</span>'

        best = laptime(r.best_lap)
        if fastest is not None and pd.notna(r.best_lap) and abs(r.best_lap - fastest) < 1e-6:
            best = sector_cell(r.best_lap, "purple").replace(f"{r.best_lap:.3f}", laptime(r.best_lap))
        last = laptime(r.last_lap)
        if pd.notna(r.last_lap) and pd.notna(r.best_lap) and abs(r.last_lap - r.best_lap) < 1e-6:
            status = "purple" if fastest is not None and abs(r.last_lap - fastest) < 1e-6 else "green"
            last = sector_cell(r.last_lap, status).replace(f"{r.last_lap:.3f}", laptime(r.last_lap))

        cells = [str(int(r.position)), driver_cell(r.driver_number, r.name_acronym, r.team_name, r.team_colour)]
        if is_race:
            cells += [tag or gap_text(r.gap), "" if tag else gap_text(r.interval, "")]
        else:
            cells[1] += f" {tag}"
        cells += [
            tyre_icon(r.compound, r.tyre_age) + (f' <span class="pl-stops">{r.pits}×</span>' if r.pits else ""),
            last,
            sector_cell(r.s1, r.s1_status),
            sector_cell(r.s2, r.s2_status),
            sector_cell(r.s3, r.s3_status),
            best,
        ]
        if show_model:
            gain = r.pit_gain
            cells.append(
                "" if pd.isna(gain) else f'<span class="pl-good">+{gain:.1f}s</span>' if gain > 0 else f'<span class="pl-bad">{gain:.1f}s</span>'
            )
        rows.append((cells, "out" if r.out else ""))
    return table(headers, rows)


def render(
    data: race.RaceData,
    state: race.RaceState,
    map_args: dict[str, Any],
    outline: pd.DataFrame,
    key: str,
    total_laps: int | None,
    deg: tuple[dict[str, float], str] | None,
) -> None:
    is_race = data.session.get("session_name") in RACE_TYPES
    remaining = (total_laps - state.leader_lap) if (is_race and total_laps) else None
    show_model = bool(is_race and deg and remaining and remaining > 0)
    if show_model:
        loss = race.pit_loss(data, state.t)
        state.tower = race.add_pit_model(state.tower, deg[0], remaining, loss)

    html(flag_banner(state.status, state.leader_lap, total_laps))
    left, right = st.columns([13, 7], gap="small")
    with left:
        html(tower_html(state, is_race, show_model))
        html(sector_legend())
        if show_model:
            st.caption(
                f"**Pit now?** Time gained by pitting now instead of never stopping again, for the "
                f"{remaining} laps left: tyre wear × tyre age × laps left − {loss:.1f}s pit loss. "
                f"Wear per lap from {deg[1]}. Green means fresh tyres would pay off."
            )
    with right:
        track_map(outline=outline, tower=state.tower, key=f"map_{key}", **map_args)
        by_number = state.tower.set_index("driver_number")
        items = []
        if state.fastest_lap:
            f = state.fastest_lap
            r = by_number.loc[f["driver_number"]]
            items.append({"label": f"Fastest lap · L{f['lap']}", "value": laptime(f["time"]), "cls": "purple",
                          "sub": driver_cell(f["driver_number"], r["name_acronym"], r["team_name"], r["team_colour"])})
        for n, best in sorted(state.best_sectors.items()):
            if best["driver_number"] in by_number.index:
                r = by_number.loc[best["driver_number"]]
                items.append({"label": f"Best sector {n}", "value": f"{best['time']:.3f}", "cls": "purple",
                              "sub": driver_cell(best["driver_number"], r["name_acronym"], r["team_name"], r["team_colour"], show_team=False)})
        if items:
            html(cards(items))
        st.markdown("**Race control**")
        html(messages_html(state.messages))


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    mode = st.segmented_control("Mode", ["Replay", "Live"], default="Replay", key="tracker_mode") or "Replay"


def default_meeting_index() -> int:
    """The weekend of the most recent race that has finished."""
    meetings = load_meetings(now.year)
    try:
        races = load_year_sessions(now.year, "Race")
    except OpenF1LockedError:
        return len(meetings) - 1
    done = races[races["date_end"] < pd.Timestamp(now)] if not races.empty else races
    if done.empty or meetings.empty:
        return len(meetings) - 1
    key = done.sort_values("date_start").iloc[-1]["meeting_key"]
    match = meetings.index[meetings["meeting_key"] == key]
    return int(match[0]) if len(match) else len(meetings) - 1


# ---------------------------------------------------------------- replay
if mode == "Replay":
    with st.sidebar:
        meeting, sessions_df = meeting_picker("tracker", default_meeting_index())
        finished = sessions_df[sessions_df["date_end"] < pd.Timestamp(now)]
        if finished.empty:
            st.info("No finished session in this weekend yet.")
            st.stop()
        names = finished["session_name"].tolist()
        default = next((n for n in ("Race", "Sprint") if n in names), names[-1])
        session_name = st.selectbox("Session", names, index=names.index(default), key=f"tracker_session_{meeting['meeting_key']}")
    session = finished[finished["session_name"] == session_name].iloc[0]
    session_dict = {k: (v.isoformat() if isinstance(v, pd.Timestamp) else v) for k, v in session.to_dict().items()}
    data = load_race(session_dict)
    if data.t_start is None or data.laps.empty:
        st.info("This session has no timing data on OpenF1 yet.")
        st.stop()

    key = f"replay_{session['session_key']}"
    if key not in st.session_state:
        st.session_state[key] = {"t": data.t_start, "playing": False, "tick": None, "seek_id": 0}
        st.session_state[f"{key}_store"] = race.LocationStore(client, int(session["session_key"]))
    rs: dict[str, Any] = st.session_state[key]
    store: race.LocationStore = st.session_state[f"{key}_store"]
    outline = load_outline(session_dict, data.laps)
    deg = practice_degradation(int(meeting["meeting_key"])) if session_name in RACE_TYPES else None

    st.title(f"{meeting['meeting_name']} · {session_name}")
    st.caption(f"Replay of the {session['date_start']:%d %b %Y} session · data from OpenF1")

    def seek(delta_s: float | None = None, to: pd.Timestamp | None = None) -> None:
        t = to if to is not None else rs["t"] + pd.Timedelta(seconds=delta_s or 0)
        rs["t"] = min(max(t, data.t_start), data.t_end)
        rs["tick"] = time.monotonic() if rs["playing"] else None
        rs["seek_id"] = rs.get("seek_id", 0) + 1

    def jump_to_lap() -> None:
        lap = st.session_state.get(f"{key}_lap")
        start = data.lap_start_time(int(lap)) if lap else None
        if start is not None:
            seek(to=start)

    c1, c2, c3, c4, c5, c6 = st.columns([1, 1, 1.4, 1, 1.6, 2], vertical_alignment="bottom")
    c1.button("⏮", help="Back to the start", on_click=seek, kwargs={"to": data.t_start}, width="stretch")
    c2.button("−30s", on_click=seek, kwargs={"delta_s": -30}, width="stretch")

    def toggle() -> None:
        rs["playing"] = not rs["playing"]
        rs["tick"] = time.monotonic() if rs["playing"] else None
        if rs["playing"] and rs["t"] >= data.t_end:
            rs["t"] = data.t_start

    c3.button("⏸ Pause" if rs["playing"] else "▶ Play", type="primary", on_click=toggle, width="stretch")
    c4.button("+30s", on_click=seek, kwargs={"delta_s": 30}, width="stretch")
    speed = c5.select_slider("Speed", SPEEDS, value=10, format_func=lambda s: f"{s}×", key=f"{key}_speed")
    c6.selectbox(
        "Jump to lap",
        list(range(1, int(data.laps["lap_number"].max()) + 1)),
        index=None,
        placeholder="Lap…",
        key=f"{key}_lap",
        on_change=jump_to_lap,
    )

    @st.fragment(run_every=1.0 if rs["playing"] else None)
    def replay_view() -> None:
        if rs["playing"] and rs["tick"] is not None:
            tick = time.monotonic()
            rs["t"] = rs["t"] + pd.Timedelta(seconds=(tick - rs["tick"]) * speed)
            rs["tick"] = tick
            if rs["t"] >= data.t_end:
                rs["t"] = data.t_end
                rs["playing"] = False
                st.rerun()  # full rerun: stops the timer and resets the Play button
        t = rs["t"]
        elapsed = t - data.t_start
        total = data.t_end - data.t_start
        progress = min(max(elapsed / total, 0.0), 1.0) if total.total_seconds() > 0 else 0.0
        mins, secs = divmod(int(elapsed.total_seconds()), 60)
        st.progress(progress, text=f"Session time {mins // 60}:{mins % 60:02d}:{secs:02d} · {t:%H:%M:%S} UTC")
        state = race.state_at(data, t)
        # Send the next few seconds of car positions; the browser animates them.
        ahead = pd.Timedelta(seconds=speed * 2.5 + 3 if rs["playing"] else 2)
        samples = store.window(t - pd.Timedelta(seconds=3), t + ahead)
        map_args = {
            "samples": samples,
            "base": data.t_start,
            "playhead": t,
            "rate": speed if rs["playing"] else 0,
            "seek_id": rs.get("seek_id", 0),
        }
        render(data, state, map_args, outline, key, data.total_laps, deg)

    replay_view()

# ---------------------------------------------------------------- live
else:
    st.title("Live timing")
    if not client.authenticated:
        st.info(
            "Live data during a session needs an OpenF1 sponsor account (€9.90/month, "
            "[openf1.org](https://openf1.org)). Once you have one, set your credentials as "
            "environment variables and restart the app:"
        )
        st.code("export OPENF1_USERNAME='you@example.com'\nexport OPENF1_PASSWORD='…'\nuv run streamlit run app.py", language="bash")
        st.caption("Until then, use **Replay**: it shows finished sessions exactly as live mode would.")

    meetings = load_meetings(now.year)
    meeting = meetings.iloc[min(len(meetings) - 1, max(0, int((meetings["date_start"] <= pd.Timestamp(now)).sum()) - 1))]
    sessions_df = load_sessions(int(meeting["meeting_key"]))
    window_open = sessions_df["date_start"] - pd.Timedelta(minutes=15) <= pd.Timestamp(now)
    window_close = sessions_df["date_end"] + pd.Timedelta(minutes=30) >= pd.Timestamp(now)
    live = sessions_df[window_open & window_close]
    upcoming = sessions_df[sessions_df["date_start"] > pd.Timestamp(now)]

    if live.empty:
        if not upcoming.empty:
            nxt = upcoming.iloc[0]
            wait = nxt["date_start"] - pd.Timestamp(now)
            hours, rem = divmod(int(wait.total_seconds()), 3600)
            st.info(f"No session running. Next: **{meeting['meeting_name']} · {nxt['session_name']}** at {nxt['date_start']:%a %d %b, %H:%M} UTC (in {hours}h {rem // 60}m).")
        else:
            st.info("No session running right now.")
        st.stop()
    if not client.authenticated:
        st.stop()

    session = live.iloc[0]
    session_dict = {k: (v.isoformat() if isinstance(v, pd.Timestamp) else v) for k, v in session.to_dict().items()}
    key = f"live_{session['session_key']}"
    if key not in st.session_state:
        st.session_state[key] = race.LiveFeed(client, session_dict)
    feed: race.LiveFeed = st.session_state[key]
    is_race = session["session_name"] in RACE_TYPES
    with st.sidebar:
        total_laps = st.number_input("Race distance (laps)", 0, 80, 57 if is_race else 0, help="Used for the pit-stop model. 0 turns it off.") or None
    deg = practice_degradation(int(meeting["meeting_key"])) if is_race else None
    st.caption(f"{meeting['meeting_name']} · {session['session_name']} · updates every {LIVE_POLL_S}s")

    @st.fragment(run_every=LIVE_POLL_S)
    def live_view() -> None:
        feed.poll()
        newest = feed.location_time() or feed.data_time()
        if newest is None:
            st.info("Waiting for the first data of the session…")
            return
        t = newest - pd.Timedelta(seconds=LIVE_DELAY_S)
        data = feed.data()
        outline = load_outline(session_dict, data.laps) if not data.laps.empty else pd.DataFrame(columns=["x", "y"])
        state = race.state_at(data, t)
        st.caption(f"Showing {t:%H:%M:%S} UTC · {LIVE_DELAY_S}s behind the feed so cars move smoothly")
        map_args = {
            "samples": feed.location_window(t - pd.Timedelta(seconds=3), newest),
            "base": data.t_start or t,
            "playhead": t,
            "rate": 1.0,
            "seek_id": 0,
        }
        render(data, state, map_args, outline, key, total_laps, deg)

    live_view()
