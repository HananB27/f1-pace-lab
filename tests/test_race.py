import pandas as pd
import pytest

from pacelab import race
from pacelab.client import OpenF1Client, RateLimiter
from pacelab.teams import colour_emoji, driver_label, team_code

T0 = pd.Timestamp("2026-11-29T16:00:00Z")


def ts(seconds: float) -> str:
    return (T0 + pd.Timedelta(seconds=seconds)).isoformat()


def lap(driver, number, start_s, s1, s2, s3, pit_out=False):
    return {
        "driver_number": driver,
        "lap_number": number,
        "date_start": ts(start_s),
        "lap_duration": None if None in (s1, s2, s3) else s1 + s2 + s3,
        "duration_sector_1": s1,
        "duration_sector_2": s2,
        "duration_sector_3": s3,
        "i1_speed": 250,
        "i2_speed": 280,
        "st_speed": 320,
        "is_pit_out_lap": pit_out,
    }


@pytest.fixture
def data():
    raw = {
        "drivers": [
            {"driver_number": 1, "name_acronym": "AAA", "full_name": "A", "team_name": "McLaren", "team_colour": "F47600"},
            {"driver_number": 2, "name_acronym": "BBB", "full_name": "B", "team_name": "Ferrari", "team_colour": "E80020"},
        ],
        "laps": [
            lap(1, 1, 0, 30.0, 30.0, 30.0),
            lap(1, 2, 90, 29.0, 31.0, 30.0),
            lap(1, 3, 180, 29.5, 30.5, 30.0),
            lap(2, 1, 0, 30.5, 29.5, 31.0),
            lap(2, 2, 91, 29.8, 30.2, 30.5),
        ],
        "stints": [
            {"driver_number": 1, "stint_number": 1, "lap_start": 1, "lap_end": 3, "compound": "MEDIUM", "tyre_age_at_start": 0},
            {"driver_number": 2, "stint_number": 1, "lap_start": 1, "lap_end": 2, "compound": "SOFT", "tyre_age_at_start": 2},
        ],
        "pit": [{"driver_number": 2, "lap_number": 2, "date": ts(170), "lane_duration": 21.0}],
        "position": [
            {"driver_number": 1, "date": ts(-60), "position": 1},
            {"driver_number": 2, "date": ts(-60), "position": 2},
        ],
        "intervals": [
            {"driver_number": 1, "date": ts(100), "gap_to_leader": 0.0, "interval": 0.0},
            {"driver_number": 2, "date": ts(100), "gap_to_leader": 1.2, "interval": 1.2},
        ],
        "race_control": [
            {"date": ts(50), "category": "SafetyCar", "flag": None, "scope": None, "lap_number": 1, "message": "SAFETY CAR DEPLOYED"},
            {"date": ts(120), "category": "SafetyCar", "flag": None, "scope": None, "lap_number": 2, "message": "SAFETY CAR IN THIS LAP"},
        ],
    }
    session = {"session_key": 1, "session_name": "Race", "date_end": "2020-01-01T00:00:00+00:00"}
    return race.build_race_data(session, raw)


def test_state_shows_latest_sectors_with_timing_colours(data):
    # 120s: driver 1 is on lap 2 with S1 (29.0) done; driver 2 has S1 29.8 on lap 2.
    state = race.state_at(data, T0 + pd.Timedelta(seconds=125))
    tower = state.tower.set_index("driver_number")

    assert tower.loc[1, "s1"] == 29.0
    assert tower.loc[1, "s1_status"] == "purple"
    assert tower.loc[2, "s1"] == 29.8
    assert tower.loc[2, "s1_status"] == "green"  # own best, but slower than 29.0
    assert pd.isna(tower.loc[1, "s2"])  # not reached yet
    assert state.best_sectors[1] == {"time": 29.0, "driver_number": 1}


def test_state_tyres_laps_and_pits(data):
    state = race.state_at(data, T0 + pd.Timedelta(seconds=175))
    tower = state.tower.set_index("driver_number")

    assert tower.loc[1, "compound"] == "MEDIUM"
    assert tower.loc[1, "tyre_age"] == 1  # lap 2 of a new set
    assert tower.loc[2, "tyre_age"] == 3
    assert tower.loc[2, "pits"] == 1
    assert bool(tower.loc[2, "in_pit"])  # entered at 170s, lane takes 21s
    assert tower.loc[1, "last_lap"] == pytest.approx(90.0)
    assert state.leader_lap == 2


def test_before_start_shows_grid_and_starting_tyres(data):
    state = race.state_at(data, T0 - pd.Timedelta(seconds=30))
    tower = state.tower.set_index("driver_number")
    assert tower.loc[1, "position"] == 1
    assert tower.loc[2, "compound"] == "SOFT"
    assert state.leader_lap == 0


def test_track_status_safety_car_then_restart(data):
    rc = data.race_control
    assert race.track_status(rc, T0 + pd.Timedelta(seconds=60), leader_lap=1) == "SC"
    assert race.track_status(rc, T0 + pd.Timedelta(seconds=130), leader_lap=2) == "SC ENDING"
    # The safety car comes in at the end of the announced lap.
    assert race.track_status(rc, T0 + pd.Timedelta(seconds=200), leader_lap=3) == "GREEN"


def test_pit_model_gain():
    tower = pd.DataFrame({"compound": ["SOFT", "HARD"], "tyre_age": [20, 2]})
    out = race.add_pit_model(tower, {"SOFT": 0.1, "HARD": 0.05}, remaining_laps=15, loss=22.0)
    # 0.1 * 20 * 15 - 22 = 8 s gained by pitting now; the fresh Hard should stay out.
    assert out["pit_gain"].tolist() == pytest.approx([8.0, 0.05 * 2 * 15 - 22.0])


def test_rate_limiter_respects_per_minute_budget():
    now = [0.0]
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(per_second=100, per_minute=3, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert slept == [pytest.approx(0.01), pytest.approx(0.01)]
    limiter.wait()  # 4th call must wait for the first to leave the 60s window
    assert now[0] == pytest.approx(60.0)


def test_filter_operators_are_encoded_without_equals(tmp_path):
    client = OpenF1Client(cache_path=tmp_path / "c.sqlite")
    url = client._url("location", {"session_key": 9, "date>": "2026-11-29T16:00:00+00:00", "date<=": "x"})
    assert "date%3E2026-11-29T16:00:00%2B00:00" in url
    assert "date%3C%3Dx" in url
    assert "session_key=9" in url


def test_team_helpers():
    assert team_code("Red Bull Racing") == "RBR"
    assert team_code("Some New Team") == "SOM"
    assert colour_emoji("#F47600") == "🟠"
    assert driver_label(4, "NOR", "McLaren", "#F47600") == "🟠 #4 NOR · McLaren"


class FakeClient:
    """Records requests and serves canned rows, for testing the live poll schedule."""

    def __init__(self):
        self.calls = []

    def get(self, endpoint, params, ttl=None, cache=True):
        self.calls.append(endpoint)
        if endpoint in ("position", "intervals"):
            return [{"driver_number": 1, "date": ts(len(self.calls)), "position": 1, "gap_to_leader": 0, "interval": 0}]
        if endpoint == "location":
            return [{"driver_number": 1, "date": ts(len(self.calls) + i * 0.27), "x": 10 * i, "y": 5} for i in range(3)]
        return []


def test_live_feed_request_budget():
    client = FakeClient()
    feed = race.LiveFeed(client, {"session_key": 1, "session_name": "Race"})
    for _ in range(20):  # one minute at POLL_S = 3
        feed.poll()
    per_minute = len(client.calls) - client.calls.count("drivers")
    assert client.calls.count("location") == 20
    assert per_minute <= 56  # sponsor limit is 60/minute, with headroom
    assert not feed.locations.empty


def test_car_samples_payload():
    from views.track_map import car_samples

    df = pd.DataFrame(
        {
            "driver_number": [4, 4, 81],
            "date": pd.to_datetime([ts(1), ts(1.27), ts(1.1)], utc=True, format="ISO8601"),
            "x": [100, 110, -5],
            "y": [0, 2, 7],
        }
    )
    out = car_samples(df, T0)
    assert out["4"] == {"t": [1.0, 1.27], "x": [100.0, 110.0], "y": [0.0, 2.0]}
    assert out["81"]["t"] == [1.1]
