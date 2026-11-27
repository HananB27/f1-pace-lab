import numpy as np
import pandas as pd
import pytest

from pacelab import analysis
from pacelab.data import enrich_laps


def make_laps(driver: int, times: list[float | None], compound: str = "MEDIUM", start_lap: int = 1, **extra):
    rows = []
    for i, t in enumerate(times):
        rows.append(
            {
                "driver_number": driver,
                "lap_number": start_lap + i,
                "date_start": f"2026-11-27T13:{30 + i:02d}:00+00:00",
                "lap_duration": t,
                "duration_sector_1": None if t is None else t * 0.35,
                "duration_sector_2": None if t is None else t * 0.35,
                "duration_sector_3": None if t is None else t * 0.30,
                "i1_speed": 250,
                "i2_speed": 280,
                "st_speed": 320,
                "is_pit_out_lap": i == 0,
            }
        )
    return rows


def build(laps_rows, stints_rows, pits_rows=(), drivers_rows=()):
    df = enrich_laps(
        pd.DataFrame(laps_rows),
        pd.DataFrame(stints_rows),
        pd.DataFrame(list(pits_rows)),
        pd.DataFrame(list(drivers_rows)),
    )
    df["session_key"] = 1
    df["session_name"] = "Practice 2"
    return df


def test_enrich_assigns_compound_and_tyre_age():
    laps = make_laps(4, [None, 90.0, 89.5, 89.6])
    stints = [{"driver_number": 4, "stint_number": 1, "lap_start": 1, "lap_end": 4, "compound": "SOFT", "tyre_age_at_start": 2}]
    pits = [{"driver_number": 4, "lap_number": 4}]
    drivers = [{"driver_number": 4, "name_acronym": "NOR", "full_name": "Lando NORRIS", "team_name": "McLaren", "team_colour": "F47600"}]
    df = build(laps, stints, pits, drivers)

    assert df["compound"].tolist() == ["SOFT"] * 4
    assert df["tyre_age"].tolist() == [2, 3, 4, 5]
    assert df["is_in_lap"].tolist() == [False, False, False, True]
    assert df["team_colour"].iloc[0] == "#F47600"
    # Out-lap, in-lap and untimed laps are excluded from clean laps.
    assert analysis.clean_laps(df)["lap_number"].tolist() == [2, 3]


def test_short_run_table_orders_by_best_lap_and_computes_gaps():
    laps = make_laps(1, [None, 85.0, 84.0]) + make_laps(2, [None, 84.5, 83.5])
    stints = [
        {"driver_number": d, "stint_number": 1, "lap_start": 1, "lap_end": 3, "compound": "SOFT", "tyre_age_at_start": 0}
        for d in (1, 2)
    ]
    table = analysis.short_run_table(build(laps, stints))

    assert table["driver_number"].tolist() == [2, 1]
    assert table["gap"].tolist() == pytest.approx([0.0, 0.5])
    assert table["ideal_lap"].iloc[0] == pytest.approx(83.5)


def test_long_run_detection_skips_qualifying_pattern():
    # Race sim: steady laps with 0.1s/lap of real degradation, fuel burn hides 0.05s/lap.
    race_sim = [None] + [90.0 + 0.05 * i for i in range(10)]
    # Quali runs: push / cool-down alternating, should not count as a long run.
    quali = [None, 84.0, 110.0, 84.2, 110.0, 84.1, 110.0, 84.3, 110.0, 84.2, 110.0]
    laps = make_laps(1, race_sim) + make_laps(2, quali)
    stints = [
        {"driver_number": d, "stint_number": 1, "lap_start": 1, "lap_end": 11, "compound": "MEDIUM", "tyre_age_at_start": 0}
        for d in (1, 2)
    ]
    df = build(laps, stints)
    run_laps = analysis.find_long_runs(df, analysis.LongRunParams(fuel_effect_s_per_lap=0.05))

    assert set(run_laps["driver_number"]) == {1}
    runs = analysis.long_run_table(run_laps)
    assert len(runs) == 1
    assert runs["laps"].iloc[0] == 10
    # 0.05 observed + 0.05 fuel correction = 0.10 s/lap of tyre degradation.
    assert runs["deg_s_per_lap"].iloc[0] == pytest.approx(0.10, abs=1e-6)


def test_long_run_tolerates_single_traffic_lap():
    times = [None, 90.0, 90.1, 90.2, 97.0, 90.3, 90.4, 90.5]
    stints = [{"driver_number": 1, "stint_number": 1, "lap_start": 1, "lap_end": 8, "compound": "HARD", "tyre_age_at_start": 0}]
    run_laps = analysis.find_long_runs(build(make_laps(1, times), stints))

    assert run_laps["run_id"].nunique() == 1
    assert 5 not in run_laps["lap_number"].tolist()  # the traffic lap is dropped
    assert len(run_laps) == 6


def test_compound_degradation_pools_runs_of_different_speed():
    laps, stints = [], []
    for d, base in ((1, 90.0), (2, 91.0)):
        laps += make_laps(d, [None] + [base + 0.2 * i for i in range(8)])
        stints.append({"driver_number": d, "stint_number": 1, "lap_start": 1, "lap_end": 9, "compound": "SOFT", "tyre_age_at_start": 0})
    run_laps = analysis.find_long_runs(build(laps, stints), analysis.LongRunParams(fuel_effect_s_per_lap=0.0, push_tolerance=0.05))
    deg = analysis.compound_degradation(run_laps)

    assert deg["deg_s_per_lap"].iloc[0] == pytest.approx(0.2)
    assert deg["runs"].iloc[0] == 2


@pytest.mark.parametrize(
    "seconds, expected",
    [(83.217, "1:23.217"), (59.5, "59.500"), (np.nan, "-"), (None, "-")],
)
def test_format_laptime(seconds, expected):
    assert analysis.format_laptime(seconds) == expected
