"""Practice-session pace analysis: short runs, long runs and tyre degradation.

Practice data is noisy: teams run different fuel loads, engine modes and programmes,
and nobody publishes those. Everything here is an estimate, and the assumptions are
explicit parameters so they can be tuned and challenged.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

SECTORS = ["duration_sector_1", "duration_sector_2", "duration_sector_3"]


@dataclass(frozen=True)
class LongRunParams:
    # A lap counts as representative if it is within this fraction of the
    # fastest clean lap of its stint (filters out cool-down and traffic laps).
    push_tolerance: float = 0.04
    # Minimum representative laps for a run to count as a race simulation.
    min_laps: int = 5
    # Share of laps in a run that must be representative. Qualifying runs alternate
    # push / cool-down laps (~50%), race simulations are almost all push laps.
    min_push_ratio: float = 0.75
    # Lap time gained per lap from burning fuel. Added back so that the slope of
    # corrected lap time vs tyre age is (roughly) pure tyre degradation.
    fuel_effect_s_per_lap: float = 0.05


def clean_laps(laps: pd.DataFrame) -> pd.DataFrame:
    """Timed laps that are not out-laps or in-laps."""
    if laps.empty:
        return laps
    mask = laps["lap_duration"].notna() & ~laps["is_pit_out_lap"] & ~laps["is_in_lap"]
    return laps[mask]


def short_run_table(laps: pd.DataFrame) -> pd.DataFrame:
    """One row per driver: best lap, best sectors, ideal lap and top speed.

    The order is a rough qualifying prediction, but only for drivers who did a
    proper push lap on soft tyres. The compound column shows which ones did.
    """
    clean = clean_laps(laps)
    if clean.empty:
        return pd.DataFrame()

    best_idx = clean.groupby("driver_number")["lap_duration"].idxmin()
    best = clean.loc[best_idx].set_index("driver_number")

    out = pd.DataFrame(
        {
            "driver": best["name_acronym"],
            "team": best["team_name"],
            "team_colour": best["team_colour"],
            "best_lap": best["lap_duration"],
            "compound": best["compound"],
            "tyre_age": best["tyre_age"],
            "session": best.get("session_name"),
        }
    )

    sector_best = clean.groupby("driver_number")[SECTORS].min()
    out = out.join(sector_best.set_axis(["s1", "s2", "s3"], axis=1))
    out["ideal_lap"] = sector_best.sum(axis=1, min_count=3)
    out["laps"] = laps.groupby("driver_number")["lap_number"].count()
    out["top_speed"] = laps.groupby("driver_number")["st_speed"].max()

    out = out.sort_values("best_lap").reset_index()
    out.insert(0, "pos", range(1, len(out) + 1))
    out["gap"] = out["best_lap"] - out["best_lap"].iloc[0]
    out["ideal_gap"] = out["ideal_lap"] - out["ideal_lap"].min()
    for n, (col, sector) in enumerate(zip(("s1", "s2", "s3"), SECTORS), start=1):
        out[f"{col}_gap"] = out[col] - out[col].min()
        # Sector times of the best lap itself, coloured like the F1 timing screen.
        lap_sector = out["driver_number"].map(best[sector])
        out[f"lap_s{n}"] = lap_sector
        out[f"lap_s{n}_status"] = [
            sector_colour(value, own_best, out[col].min())
            for value, own_best in zip(lap_sector, out[col])
        ]
    return out


def sector_colour(value: float, personal_best: float, overall_best: float) -> str:
    """'purple' = fastest of anyone, 'green' = driver's own best, 'yellow' = slower."""
    if pd.isna(value):
        return ""
    if pd.notna(overall_best) and np.isclose(value, overall_best):
        return "purple"
    if pd.notna(personal_best) and np.isclose(value, personal_best):
        return "green"
    return "yellow"


def sector_leaderboard(laps: pd.DataFrame, sector: int) -> pd.DataFrame:
    """Each driver's best time in one sector (1-3), fastest first."""
    clean = clean_laps(laps)
    col = SECTORS[sector - 1]
    clean = clean[clean[col].notna()]
    if clean.empty:
        return pd.DataFrame()
    best = clean.loc[clean.groupby("driver_number")[col].idxmin()]
    out = pd.DataFrame(
        {
            "driver_number": best["driver_number"],
            "driver": best["name_acronym"],
            "team": best["team_name"],
            "team_colour": best["team_colour"],
            "time": best[col],
            "compound": best["compound"],
            "tyre_age": best["tyre_age"],
            "lap_number": best["lap_number"],
        }
    ).sort_values("time").reset_index(drop=True)
    out["gap"] = out["time"] - out["time"].iloc[0]
    return out


def find_long_runs(laps: pd.DataFrame, params: LongRunParams = LongRunParams()) -> pd.DataFrame:
    """Return the representative laps of every detected race-simulation run.

    Adds run_id, lap_in_run and fuel_corrected columns.
    """
    clean = clean_laps(laps)
    clean = clean[clean["stint_number"].notna()]
    if clean.empty:
        return pd.DataFrame()

    group_cols = ["session_key", "driver_number", "stint_number"]
    runs: list[pd.DataFrame] = []
    for _, stint in clean.groupby(group_cols, sort=False):
        stint = stint.sort_values("lap_number")
        limit = stint["lap_duration"].min() * (1 + params.push_tolerance)
        push = stint[stint["lap_duration"] <= limit]
        if len(push) < params.min_laps:
            continue

        # Split into segments; allow one non-push lap (traffic, a mistake) inside a run.
        segment = (push["lap_number"].diff() > 2).cumsum()
        for _, seg in push.groupby(segment):
            span = seg["lap_number"].iloc[-1] - seg["lap_number"].iloc[0] + 1
            if len(seg) < params.min_laps or len(seg) / span < params.min_push_ratio:
                continue
            seg = seg.copy()
            seg["lap_in_run"] = seg["lap_number"] - seg["lap_number"].iloc[0]
            seg["fuel_corrected"] = (
                seg["lap_duration"] + params.fuel_effect_s_per_lap * seg["lap_in_run"]
            )
            runs.append(seg)

    if not runs:
        return pd.DataFrame()
    out = pd.concat(runs, ignore_index=True)
    key = out[["session_key", "driver_number", "stint_number"]].astype(str).agg("-".join, axis=1)
    first_lap = out.groupby(key)["lap_number"].transform("min").astype(int).astype(str)
    out["run_id"] = key + "-L" + first_lap
    return out


def long_run_table(run_laps: pd.DataFrame) -> pd.DataFrame:
    """One row per detected run: pace, consistency and degradation."""
    if run_laps.empty:
        return pd.DataFrame()

    rows = []
    for run_id, run in run_laps.groupby("run_id"):
        first = run.iloc[0]
        x = run["tyre_age"].fillna(run["lap_in_run"]).to_numpy(dtype=float)
        y = run["fuel_corrected"].to_numpy(dtype=float)
        deg = float(np.polyfit(x, y, 1)[0]) if len(np.unique(x)) > 1 else np.nan
        rows.append(
            {
                "run_id": run_id,
                "driver_number": int(first["driver_number"]),
                "driver": first["name_acronym"],
                "team": first["team_name"],
                "team_colour": first["team_colour"],
                "session": first.get("session_name"),
                "compound": first["compound"],
                "laps": len(run),
                "tyre_age_start": run["tyre_age"].min(),
                "tyre_age_end": run["tyre_age"].max(),
                "avg_lap": run["lap_duration"].mean(),
                "avg_corrected": run["fuel_corrected"].mean(),
                "median_lap": run["lap_duration"].median(),
                "std_dev": run["lap_duration"].std(),
                "deg_s_per_lap": deg,
            }
        )
    return pd.DataFrame(rows).sort_values("avg_corrected").reset_index(drop=True)


def team_long_run_ranking(runs: pd.DataFrame) -> pd.DataFrame:
    """Best (longest, then fastest) run per team and compound, ranked per compound."""
    if runs.empty:
        return pd.DataFrame()
    ordered = runs.sort_values(["laps", "avg_corrected"], ascending=[False, True])
    best = ordered.drop_duplicates(["team", "compound"]).copy()
    best = best.sort_values(["compound", "avg_corrected"])
    best["gap"] = best["avg_corrected"] - best.groupby("compound")["avg_corrected"].transform("min")
    best["rank"] = best.groupby("compound").cumcount() + 1
    return best[
        ["compound", "rank", "team", "driver_number", "driver", "laps", "avg_corrected", "gap", "deg_s_per_lap", "team_colour"]
    ].reset_index(drop=True)


def compound_degradation(run_laps: pd.DataFrame) -> pd.DataFrame:
    """Field-wide degradation per compound: pooled slope with per-run offsets removed."""
    if run_laps.empty:
        return pd.DataFrame()
    rows = []
    for compound, laps in run_laps.groupby("compound"):
        # Remove each run's mean so cars of different speed can be pooled.
        y = laps["fuel_corrected"] - laps.groupby("run_id")["fuel_corrected"].transform("mean")
        x = laps["tyre_age"].fillna(laps["lap_in_run"])
        x = x - x.groupby(laps["run_id"]).transform("mean")
        if len(laps) < 3 or float((x**2).sum()) == 0.0:
            continue
        slope = float((x * y).sum() / (x**2).sum())
        rows.append(
            {
                "compound": compound,
                "runs": laps["run_id"].nunique(),
                "laps": len(laps),
                "deg_s_per_lap": slope,
            }
        )
    return pd.DataFrame(rows)


def format_laptime(seconds: float | None) -> str:
    if seconds is None or pd.isna(seconds):
        return "-"
    minutes, rest = divmod(float(seconds), 60)
    return f"{int(minutes)}:{rest:06.3f}" if minutes else f"{rest:.3f}"
