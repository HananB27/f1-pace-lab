"""Python side of the animated track map (frontend in track_map_frontend/index.html)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit.components.v1 as components

_component = components.declare_component(
    "track_map", path=str(Path(__file__).parent / "track_map_frontend")
)


def car_samples(samples: pd.DataFrame, base: pd.Timestamp) -> dict[str, dict[str, list]]:
    """Position samples per driver, with time as seconds since `base`."""
    out: dict[str, dict[str, list]] = {}
    if samples.empty:
        return out
    samples = samples.sort_values("date")
    seconds = (samples["date"] - base).dt.total_seconds().round(3)
    for number, idx in samples.groupby("driver_number").groups.items():
        out[str(int(number))] = {
            "t": seconds.loc[idx].tolist(),
            "x": samples.loc[idx, "x"].astype(float).round(0).tolist(),
            "y": samples.loc[idx, "y"].astype(float).round(0).tolist(),
        }
    return out


def track_map(
    *,
    outline: pd.DataFrame,
    samples: pd.DataFrame,
    tower: pd.DataFrame,
    base: pd.Timestamp,
    playhead: pd.Timestamp,
    rate: float,
    seek_id: int,
    key: str,
    height: int = 440,
) -> None:
    """Draw the track and animate cars in the browser.

    rate: session seconds per real second (0 = paused). The browser runs the
    clock between refreshes and is pulled back if it drifts more than a little.
    """
    cars = car_samples(samples, base)
    meta = {
        str(int(r.driver_number)): {
            "label": str(r.name_acronym),
            "colour": str(r.team_colour),
            "pos": int(r.position) if pd.notna(r.position) else 99,
            "title": f"P{int(r.position) if pd.notna(r.position) else '-'} · #{int(r.driver_number)} {r.name_acronym} · {r.team_name}",
        }
        for r in tower.itertuples()
    }
    now_s = (playhead - base).total_seconds()
    # The browser never runs its clock past the last sample it has.
    horizon = max([now_s] + [c["t"][-1] for c in cars.values() if c["t"]])
    xs = outline["x"].astype(float).round(0).tolist() if not outline.empty else []
    ys = outline["y"].astype(float).round(0).tolist() if not outline.empty else []
    _component(
        outline={"x": xs, "y": ys},
        outline_key=f"{len(xs)}:{xs[0] if xs else ''}:{ys[0] if ys else ''}",
        cars=cars,
        meta=meta,
        playhead=now_s,
        rate=float(rate),
        horizon=horizon,
        tolerance=max(2.0, rate * 1.5),
        seek_id=seek_id,
        height=height,
        key=key,
        default=None,
    )
