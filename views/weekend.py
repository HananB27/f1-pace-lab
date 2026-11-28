"""Weekend analysis: one-lap pace, sector bests and race-simulation pace from practice."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from pacelab import analysis, data
from pacelab.ui import (
    cards,
    driver_cell,
    gap_text,
    laptime,
    sector_cell,
    sector_legend,
    table,
    tyre_icon,
)
from views.common import (
    driver_picker,
    html,
    load_laps,
    meeting_picker,
    now_utc,
    load_meetings,
)

PLOT_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=10, b=10),
    font=dict(color="#c9ccd3"),
)

# ---------------------------------------------------------------- sidebar
now = now_utc()
with st.sidebar:
    current = data.current_meeting_index(load_meetings(now.year), now)
    meeting, sessions_df = meeting_picker("weekend", current)
    started = sessions_df[sessions_df["date_start"] <= pd.Timestamp(now)]
    if started.empty:
        st.info("No session of this weekend has started yet.")
        st.stop()

    practice = started[started["session_type"] == "Practice"]["session_name"].tolist()
    chosen = st.pills(
        "Sessions",
        started["session_name"].tolist(),
        selection_mode="multi",
        default=practice or started["session_name"].tolist()[-1:],
        key=f"weekend_sessions_{meeting['meeting_key']}",
    )
    if not chosen:
        st.info("Pick at least one session.")
        st.stop()

    with st.expander("Long-run assumptions"):
        push_tol = st.slider("Push-lap tolerance (%)", 1.0, 8.0, 4.0, 0.5) / 100
        min_laps = st.slider("Minimum laps in a run", 3, 12, 5)
        fuel = st.slider("Fuel effect (s per lap)", 0.0, 0.12, 0.05, 0.005, format="%.3f")
        st.caption(
            "Laps within the tolerance of a stint's best lap count as push laps. "
            "The fuel effect is added back per lap, so the slope shows tyre wear, not fuel burn."
        )

    if st.button("↻ Refresh data", width="stretch", help="Fetch the latest laps after a session ends"):
        load_laps.clear()

params = analysis.LongRunParams(push_tolerance=push_tol, min_laps=min_laps, fuel_effect_s_per_lap=fuel)
selected = sessions_df[sessions_df["session_name"].isin(chosen)].reset_index(drop=True)
laps = load_laps(selected)

st.title(f"{meeting['meeting_name']}")
st.caption(f"{meeting['location']} · {' + '.join(chosen)} · data from OpenF1")

if laps.empty:
    st.info(
        "No lap data yet. On OpenF1's free tier a session's data appears shortly after it "
        "ends. Press **Refresh data** in a few minutes."
    )
    st.stop()

short = analysis.short_run_table(laps)
drivers = laps.drop_duplicates("driver_number")[["driver_number", "name_acronym", "team_name", "team_colour"]]
by_number = drivers.set_index("driver_number")


def who(number: int, extra: str = "") -> str:
    r = by_number.loc[number]
    return driver_cell(number, r["name_acronym"], r["team_name"], r["team_colour"]) + extra


# ---------------------------------------------------------------- headline cards
top = short.iloc[0]
items = [
    {
        "label": "Fastest lap",
        "value": laptime(top["best_lap"]),
        "sub": who(top["driver_number"], tyre_icon(top["compound"], top["tyre_age"], 18)),
    }
]
for n in (1, 2, 3):
    board = analysis.sector_leaderboard(laps, n)
    if not board.empty:
        b = board.iloc[0]
        items.append({"label": f"Best sector {n}", "value": f"{b['time']:.3f}", "cls": "purple", "sub": who(b["driver_number"])})
theoretical = short[["s1", "s2", "s3"]].min().sum()
items.append(
    {
        "label": "Theoretical best",
        "value": laptime(theoretical),
        "sub": f"best sectors combined · {theoretical - top['best_lap']:+.3f}",
    }
)
speed = laps.loc[laps["st_speed"].idxmax()] if laps["st_speed"].notna().any() else None
if speed is not None:
    items.append({"label": "Top speed", "value": f"{speed['st_speed']:.0f} km/h", "sub": who(speed["driver_number"])})
html(cards(items))

tab_sheet, tab_sectors, tab_long, tab_laps = st.tabs(
    ["Timing sheet", "Sector bests", "Long runs", "Lap explorer"]
)

# ---------------------------------------------------------------- timing sheet
with tab_sheet:
    st.caption(
        "Each driver's best clean lap. Its three sector times are coloured like the F1 "
        "timing screen. **Ideal** adds up the driver's best sectors from any lap. Treat the "
        "order as a qualifying prediction only for laps set on Softs: fuel loads are unknown."
    )
    html(sector_legend())
    headers = [
        ("Pos", "pos"), ("Driver", ""), ("Tyre", ""), ("Best lap", "num"), ("Gap", "num"),
        ("S1", "num"), ("S2", "num"), ("S3", "num"), ("Ideal", "num"), ("Trap km/h", "num"),
        ("Laps", "num"), ("Session", ""),
    ]
    rows = []
    for r in short.itertuples():
        rows.append(
            (
                [
                    str(r.pos),
                    driver_cell(r.driver_number, r.driver, r.team, r.team_colour),
                    tyre_icon(r.compound, r.tyre_age),
                    f"<b>{laptime(r.best_lap)}</b>",
                    gap_text(r.gap, "—"),
                    sector_cell(r.lap_s1, r.lap_s1_status),
                    sector_cell(r.lap_s2, r.lap_s2_status),
                    sector_cell(r.lap_s3, r.lap_s3_status),
                    laptime(r.ideal_lap),
                    "" if pd.isna(r.top_speed) else f"{r.top_speed:.0f}",
                    str(r.laps),
                    str(r.session),
                ],
                "",
            )
        )
    html(table(headers, rows))

# ---------------------------------------------------------------- sector bests
with tab_sectors:
    st.caption("Every driver's best time in each sector, from any clean lap. Purple is the fastest of all.")
    cols = st.columns(3)
    for n, col in zip((1, 2, 3), cols):
        board = analysis.sector_leaderboard(laps, n)
        with col:
            st.markdown(f"**Sector {n}**")
            if board.empty:
                st.caption("No times.")
                continue
            rows = [
                (
                    [
                        str(i + 1),
                        driver_cell(r.driver_number, r.driver, r.team, r.team_colour, show_team=False),
                        sector_cell(r.time, "purple" if i == 0 else "yellow"),
                        gap_text(r.gap, "—"),
                        tyre_icon(r.compound, None, 18),
                    ],
                    "",
                )
                for i, r in enumerate(board.itertuples())
            ]
            html(table([("#", "pos"), ("Driver", ""), ("Time", "num"), ("Gap", "num"), ("", "")], rows))

    with st.expander("Gap heatmap"):
        heat = short.set_index("driver")[["s1_gap", "s2_gap", "s3_gap"]].rename(
            columns={"s1_gap": "Sector 1", "s2_gap": "Sector 2", "s3_gap": "Sector 3"}
        )
        heat = heat.loc[short.sort_values("ideal_lap")["driver"]]
        fig = px.imshow(heat, color_continuous_scale="RdYlGn_r", aspect="auto", text_auto=".3f", labels={"color": "Gap (s)"})
        fig.update_layout(height=60 + 26 * len(heat), **PLOT_LAYOUT)
        st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- long runs
with tab_long:
    run_laps = analysis.find_long_runs(laps, params)
    runs = analysis.long_run_table(run_laps)
    if runs.empty:
        st.info(
            "No long runs found. Teams usually do race simulations in FP2. "
            "You can also loosen the assumptions in the sidebar."
        )
    else:
        st.caption(
            f"Race simulations: {params.min_laps}+ laps in a row within {params.push_tolerance:.1%} of the stint's best, "
            f"fuel-corrected by {params.fuel_effect_s_per_lap:.3f}s per lap. Compare runs on the same tyre."
        )
        deg = analysis.compound_degradation(run_laps)
        if not deg.empty:
            html(
                cards(
                    {
                        "label": f"{r.compound.title()} wear",
                        "value": f"{r.deg_s_per_lap:+.3f} s/lap",
                        "sub": f"{tyre_icon(r.compound, None, 18)} {r.runs} runs · {r.laps} laps",
                    }
                    for r in deg.itertuples()
                )
            )

        ranking = analysis.team_long_run_ranking(runs)
        order = ["SOFT", "MEDIUM", "HARD"]
        compounds = sorted(ranking["compound"].unique(), key=lambda c: order.index(c) if c in order else 9)
        cols = st.columns(len(compounds))
        for col, compound in zip(cols, compounds):
            with col:
                html(f'<div class="pl-title">{tyre_icon(compound)} {compound.title()}: best run per team</div>')
                part = ranking[ranking["compound"] == compound]
                rows = [
                    (
                        [
                            str(r.rank),
                            driver_cell(r.driver_number, r.driver, r.team, r.team_colour),
                            str(r.laps),
                            laptime(r.avg_corrected),
                            gap_text(r.gap, "—"),
                            f"{r.deg_s_per_lap:+.3f}",
                        ],
                        "",
                    )
                    for r in part.itertuples()
                ]
                html(table([("#", "pos"), ("Driver", ""), ("Laps", "num"), ("Pace", "num"), ("Gap", "num"), ("Wear/lap", "num")], rows))

        st.markdown("#### Lap times through each run")
        run_drivers = drivers[drivers["driver_number"].isin(runs["driver_number"])]
        picked = driver_picker(
            "Drivers", run_drivers, runs.drop_duplicates("driver_number")["driver_number"].head(6).tolist(), "long_drivers"
        )
        plot_laps = run_laps[run_laps["driver_number"].isin(picked)]
        fig = go.Figure()
        for _, run in plot_laps.groupby("run_id"):
            first = run.iloc[0]
            fig.add_trace(
                go.Scatter(
                    x=run["tyre_age"],
                    y=run["fuel_corrected"],
                    mode="lines+markers",
                    name=f"#{first['driver_number']} {first['name_acronym']} · {first['compound'].title()} · {first['session_name']}",
                    line={"color": first["team_colour"], "dash": "solid" if first["compound"] == "SOFT" else "dot" if first["compound"] == "HARD" else "dash"},
                    hovertemplate="Tyre age %{x}<br>%{y:.3f}s<extra>%{fullData.name}</extra>",
                )
            )
        fig.update_layout(
            xaxis_title="Tyre age (laps)", yaxis_title="Fuel-corrected lap (s)", height=480,
            legend=dict(orientation="h", y=-0.2), **PLOT_LAYOUT,
        )
        st.plotly_chart(fig, width="stretch")
        st.caption("Line style shows the tyre: solid = Soft, dashed = Medium, dotted = Hard.")

        st.markdown("#### Pace vs tyre wear")
        st.caption("Bottom-left is best: fast and easy on the tyres.")
        fig = go.Figure(
            go.Scatter(
                x=runs["deg_s_per_lap"],
                y=runs["avg_corrected"],
                mode="markers+text",
                text=runs["driver"],
                textposition="top center",
                marker=dict(
                    size=8 + runs["laps"] * 1.5,
                    color=runs["team_colour"],
                    symbol=runs["compound"].map({"SOFT": "circle", "MEDIUM": "diamond", "HARD": "square"}).fillna("x"),
                    line=dict(color="#0b0c10", width=1),
                ),
                customdata=runs[["compound", "laps"]],
                hovertemplate="%{text} · %{customdata[0]} · %{customdata[1]} laps<br>wear %{x:+.3f}s/lap<br>pace %{y:.3f}s<extra></extra>",
            )
        )
        fig.update_layout(xaxis_title="Tyre wear (s/lap)", yaxis_title="Avg fuel-corrected lap (s)", height=460, **PLOT_LAYOUT)
        st.plotly_chart(fig, width="stretch")
        st.caption("Marker shape: ● Soft, ◆ Medium, ■ Hard. Bigger = longer run.")

# ---------------------------------------------------------------- lap explorer
with tab_laps:
    ordered = drivers.set_index("driver_number").loc[short["driver_number"]].reset_index()
    picked = driver_picker("Drivers", ordered, ordered["driver_number"].head(4).tolist(), "explore_drivers")
    explore = analysis.clean_laps(laps)
    explore = explore[explore["driver_number"].isin(picked)]
    if explore.empty:
        st.caption("Pick drivers to compare.")
    else:
        explore = explore[explore["lap_duration"] <= explore["lap_duration"].min() * 1.15]
        fig = go.Figure()
        for number, d in explore.groupby("driver_number"):
            first = d.iloc[0]
            fig.add_trace(
                go.Scatter(
                    x=d["date_start"],
                    y=d["lap_duration"],
                    mode="markers",
                    name=f"#{number} {first['name_acronym']}",
                    marker=dict(
                        color=first["team_colour"],
                        size=9,
                        symbol=d["compound"].map({"SOFT": "circle", "MEDIUM": "diamond", "HARD": "square"}).fillna("x"),
                        line=dict(color="#0b0c10", width=1),
                    ),
                    customdata=d[["lap_number", "compound", "tyre_age"]],
                    hovertemplate="Lap %{customdata[0]} · %{customdata[1]} (%{customdata[2]}L)<br>%{y:.3f}s<extra>%{fullData.name}</extra>",
                )
            )
        fig.update_layout(xaxis_title="Time", yaxis_title="Lap time (s)", height=500, **PLOT_LAYOUT)
        st.plotly_chart(fig, width="stretch")
        st.caption("Clean laps within 115% of the fastest. Marker shape shows the tyre: ● Soft, ◆ Medium, ■ Hard.")
