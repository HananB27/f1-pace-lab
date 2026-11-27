"""F1 Pace Lab: weekend pace analysis from OpenF1 practice data.

Run with:  uv run streamlit run app.py
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from pacelab import analysis, data
from pacelab.client import OpenF1Client

st.set_page_config(page_title="F1 Pace Lab", page_icon="🏁", layout="wide")

COMPOUND_COLOURS = {
    "SOFT": "#DA291C",
    "MEDIUM": "#FFD12E",
    "HARD": "#F0F0F0",
    "INTERMEDIATE": "#43B02A",
    "WET": "#0067AD",
    "UNKNOWN": "#888888",
}


@st.cache_resource
def get_client() -> OpenF1Client:
    return OpenF1Client()


@st.cache_data(ttl=3600, show_spinner=False)
def load_meetings(year: int) -> pd.DataFrame:
    return data.meetings(get_client(), year)


@st.cache_data(ttl=600, show_spinner=False)
def load_sessions(meeting_key: int) -> pd.DataFrame:
    return data.sessions(get_client(), meeting_key)


@st.cache_data(ttl=60, show_spinner="Loading laps from OpenF1…")
def load_laps(sessions_df: pd.DataFrame) -> pd.DataFrame:
    return data.load_sessions(get_client(), sessions_df)


def fmt(seconds: float) -> str:
    return analysis.format_laptime(seconds)


def team_colour_map(df: pd.DataFrame, key: str = "driver") -> dict[str, str]:
    return dict(zip(df[key], df["team_colour"]))


# ---------------------------------------------------------------- sidebar
now = datetime.now(timezone.utc)
with st.sidebar:
    st.title("🏁 F1 Pace Lab")
    year = st.selectbox("Season", list(range(now.year, 2022, -1)))
    meetings_df = load_meetings(year)
    if meetings_df.empty:
        st.warning("No meetings found for this season.")
        st.stop()

    default_meeting = data.current_meeting_index(meetings_df, now) if year == now.year else len(meetings_df) - 1
    meeting_idx = st.selectbox(
        "Grand Prix",
        meetings_df.index,
        index=int(default_meeting),
        format_func=lambda i: f"{meetings_df.loc[i, 'meeting_name']} ({meetings_df.loc[i, 'date_start']:%d %b})",
    )
    meeting = meetings_df.loc[meeting_idx]

    sessions_df = load_sessions(int(meeting["meeting_key"]))
    started = sessions_df[sessions_df["date_start"] <= pd.Timestamp(now)]
    if started.empty:
        st.info("No session of this weekend has started yet.")
        st.stop()

    practice = started[started["session_type"] == "Practice"]["session_name"].tolist()
    chosen = st.multiselect(
        "Sessions",
        started["session_name"].tolist(),
        default=practice or started["session_name"].tolist()[-1:],
    )
    if not chosen:
        st.stop()

    with st.expander("Long-run assumptions"):
        push_tol = st.slider("Push-lap tolerance (%)", 1.0, 8.0, 4.0, 0.5) / 100
        min_laps = st.slider("Minimum laps in a run", 3, 12, 5)
        fuel = st.slider("Fuel effect (s per lap)", 0.0, 0.12, 0.05, 0.005, format="%.3f")
        st.caption(
            "Laps within the tolerance of a stint's best lap count as push laps. "
            "The fuel effect is added back per lap so the slope shows tyre wear, not fuel burn."
        )

    if st.button("Refresh data", help="Fetch the latest laps (useful during or just after a session)"):
        load_laps.clear()

params = analysis.LongRunParams(push_tolerance=push_tol, min_laps=min_laps, fuel_effect_s_per_lap=fuel)

# ---------------------------------------------------------------- data
selected = sessions_df[sessions_df["session_name"].isin(chosen)].reset_index(drop=True)
laps = load_laps(selected)

st.header(f"{meeting['meeting_name']} · {meeting['location']}")
st.caption(
    " · ".join(chosen)
    + f" · data from OpenF1 · updated {now:%H:%M} UTC"
)

if laps.empty:
    st.info(
        "No lap data yet. OpenF1's free tier publishes session data shortly after the "
        "session ends. Try **Refresh data** in a few minutes."
    )
    st.stop()

short = analysis.short_run_table(laps)
run_laps = analysis.find_long_runs(laps, params)
runs = analysis.long_run_table(run_laps)

c1, c2, c3, c4 = st.columns(4)
c1.metric(f"Fastest lap · {short['driver'].iloc[0]}", fmt(short["best_lap"].iloc[0]))
c2.metric("Drivers", int(laps["driver_number"].nunique()))
c3.metric("Timed laps", int(laps["lap_duration"].notna().sum()))
c4.metric("Long runs found", len(runs))

tab_short, tab_long, tab_sectors, tab_laps = st.tabs(
    ["⏱ Short runs", "📈 Long runs", "🧩 Sectors", "🔎 Lap explorer"]
)

# ---------------------------------------------------------------- short runs
with tab_short:
    st.subheader("One-lap pace")
    st.caption(
        "Best clean lap per driver across the selected sessions. Treat it as a qualifying "
        "prediction only for drivers whose best lap was on Softs; fuel loads are unknown."
    )
    fig = px.bar(
        short,
        x="gap",
        y="driver",
        orientation="h",
        color="driver",
        color_discrete_map=team_colour_map(short),
        hover_data={"team": True, "compound": True, "tyre_age": True, "gap": ":.3f", "driver": False},
        labels={"gap": "Gap to fastest (s)", "driver": ""},
    )
    fig.update_layout(
        showlegend=False,
        height=40 + 24 * len(short),
        # Fastest at the top: categories are drawn bottom-up.
        yaxis={"categoryorder": "array", "categoryarray": short["driver"].tolist()[::-1]},
    )
    st.plotly_chart(fig, width="stretch")

    table = short.assign(
        best=short["best_lap"].map(fmt),
        ideal=short["ideal_lap"].map(fmt),
        gap_s=short["gap"].map(lambda g: f"+{g:.3f}" if g else "—"),
        ideal_gap_s=short["ideal_gap"].map(lambda g: f"+{g:.3f}" if g else "—"),
    )[["pos", "driver", "team", "best", "gap_s", "compound", "tyre_age", "ideal", "ideal_gap_s", "top_speed", "laps", "session"]]
    st.dataframe(
        table.rename(columns={"gap_s": "gap", "ideal_gap_s": "ideal gap", "top_speed": "speed trap (km/h)"}),
        hide_index=True,
        width="stretch",
    )

# ---------------------------------------------------------------- long runs
with tab_long:
    st.subheader("Race-simulation pace")
    if runs.empty:
        st.info(
            "No long runs detected. Race simulations usually happen in FP2. "
            "You can also loosen the assumptions in the sidebar."
        )
    else:
        st.caption(
            f"Runs of {params.min_laps}+ consecutive laps within {params.push_tolerance:.1%} of the stint's best. "
            f"Pace is fuel-corrected by {params.fuel_effect_s_per_lap:.3f}s per lap. "
            "Compare runs on the same compound; teams' fuel loads still differ."
        )

        ranking = analysis.team_long_run_ranking(runs)
        compounds = [c for c in ["SOFT", "MEDIUM", "HARD"] if c in ranking["compound"].values] or ranking["compound"].unique().tolist()
        cols = st.columns(len(compounds))
        for col, compound in zip(cols, compounds):
            with col:
                st.markdown(f"**{compound.title()}**: best run per team")
                r = ranking[ranking["compound"] == compound]
                st.dataframe(
                    r.assign(
                        pace=r["avg_corrected"].map(fmt),
                        gap=r["gap"].map(lambda g: f"+{g:.3f}" if g else "—"),
                        deg=r["deg_s_per_lap"].map(lambda d: f"{d:+.3f}"),
                    )[["rank", "team", "driver", "laps", "pace", "gap", "deg"]],
                    hide_index=True,
                    width="stretch",
                )

        deg = analysis.compound_degradation(run_laps)
        if not deg.empty:
            st.markdown("**Field-wide tyre degradation** (fuel-corrected, s/lap)")
            dcols = st.columns(len(deg))
            for col, row in zip(dcols, deg.itertuples()):
                col.metric(row.compound.title(), f"{row.deg_s_per_lap:+.3f} s/lap", f"{row.runs} runs · {row.laps} laps", delta_color="off")

        st.markdown("**Lap times through each run**")
        compound_filter = st.multiselect("Compound", sorted(runs["compound"].unique()), default=sorted(runs["compound"].unique()))
        plot_laps = run_laps[run_laps["compound"].isin(compound_filter)]
        driver_colours = team_colour_map(plot_laps.rename(columns={"name_acronym": "driver"}))
        fig = go.Figure()
        for run_id, run in plot_laps.groupby("run_id"):
            first = run.iloc[0]
            fig.add_trace(
                go.Scatter(
                    x=run["tyre_age"],
                    y=run["fuel_corrected"],
                    mode="lines+markers",
                    name=f"{first['name_acronym']} · {first['compound'][0]} · {first['session_name']}",
                    line={"color": driver_colours.get(first["name_acronym"], "#888")},
                    hovertemplate="Tyre age %{x}<br>%{y:.3f}s<extra>%{fullData.name}</extra>",
                )
            )
        fig.update_layout(xaxis_title="Tyre age (laps)", yaxis_title="Fuel-corrected lap time (s)", height=520)
        st.plotly_chart(fig, width="stretch")

        st.markdown("**Pace vs degradation**: bottom-left is fast *and* easy on tyres")
        fig = px.scatter(
            runs,
            x="deg_s_per_lap",
            y="avg_corrected",
            color="driver",
            symbol="compound",
            size="laps",
            text="driver",
            color_discrete_map=team_colour_map(runs),
            labels={"deg_s_per_lap": "Degradation (s/lap)", "avg_corrected": "Avg fuel-corrected lap (s)"},
        )
        fig.update_traces(textposition="top center")
        fig.update_layout(showlegend=False, height=480)
        st.plotly_chart(fig, width="stretch")

        with st.expander("All detected runs"):
            st.dataframe(runs.drop(columns=["team_colour", "run_id"]), hide_index=True, width="stretch")

# ---------------------------------------------------------------- sectors
with tab_sectors:
    st.subheader("Where the time is")
    st.caption("Gap to the fastest time in each sector, from each driver's best sector times (ideal lap).")
    sectors = short.set_index("driver")[["s1_gap", "s2_gap", "s3_gap"]].rename(
        columns={"s1_gap": "Sector 1", "s2_gap": "Sector 2", "s3_gap": "Sector 3"}
    )
    sectors = sectors.loc[short.sort_values("ideal_lap")["driver"]]
    fig = px.imshow(
        sectors,
        color_continuous_scale="RdYlGn_r",
        aspect="auto",
        text_auto=".3f",
        labels={"color": "Gap (s)"},
    )
    fig.update_layout(height=60 + 26 * len(sectors))
    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- lap explorer
with tab_laps:
    st.subheader("Every lap")
    drivers = short["driver"].tolist()
    pick = st.multiselect("Drivers", drivers, default=drivers[:4])
    explore = analysis.clean_laps(laps)
    explore = explore[explore["name_acronym"].isin(pick)]
    if not explore.empty:
        cutoff = explore["lap_duration"].min() * 1.15
        explore = explore[explore["lap_duration"] <= cutoff]
        fig = px.scatter(
            explore,
            x="date_start",
            y="lap_duration",
            color="name_acronym",
            symbol="compound",
            color_discrete_map=team_colour_map(explore.rename(columns={"name_acronym": "driver"})),
            hover_data=["lap_number", "tyre_age", "session_name"],
            labels={"date_start": "Time", "lap_duration": "Lap time (s)", "name_acronym": "Driver"},
        )
        fig.update_layout(height=520)
        st.plotly_chart(fig, width="stretch")
        st.caption("Clean laps within 115% of the fastest shown; out-laps and in-laps removed.")
