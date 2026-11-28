# F1 Pace Lab

A Formula 1 race-weekend app built on [OpenF1](https://openf1.org) data, with two pages:

- **Weekend analysis:** what practice tells you about the weekend. Shows one-lap pace,
  sector bests, race-simulation pace and tyre wear.
- **Race tracker:** a timing tower and a track map with every car on it. It replays any
  finished session, or follows a session live with an OpenF1 sponsor account.

## Run it

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run streamlit run app.py
```

Run the tests:

```bash
uv run pytest
```

## Weekend analysis

- **Headline cards:** fastest lap, best time in each sector (purple), theoretical best
  lap, top speed.
- **Timing sheet:** each driver's best lap, its sectors coloured like the F1 timing
  screen, the tyre it was set on and how old it was, and the ideal lap.
- **Sector bests:** a leaderboard for each sector, plus a gap heatmap.
- **Long runs:** race simulations detected in practice, fuel-corrected and ranked per
  team and compound, with tyre wear per lap.
- **Lap explorer:** every clean lap for the drivers you pick.

Driver pickers show the team colour, car number and team, for example `🟠 #4 NOR · McLaren`.
Tables use team badges and tyre icons.

| Colour | Meaning |
|---|---|
| Purple | Fastest of anyone |
| Green | The driver's own best |
| Yellow | Slower than the driver's own best |

## Race tracker

- **Timing tower:** position, gap and interval, tyre with age and number of stops, last
  lap, live sector colours and best lap. A driver in the pit lane gets a PIT tag; a
  retired driver gets OUT.
- **Track map:** the track drawn from a real lap of position data, with every car in its
  team colour. Cars are animated in the browser at the screen's frame rate: the server
  sends the next few seconds of position samples (OpenF1 records about 3.7 per car per
  second), and the page moves each car between samples. The tower refreshes every second.
- **Track status:** green flag, safety car, VSC, red flag or chequered flag, plus the latest
  race control messages.
- **Pit now?** For each driver: wear per lap × tyre age × laps left − pit-lane loss. Wear
  per lap comes from that weekend's practice long runs, and pit-lane loss from the
  session's own pit stops. Green means fresh tyres would pay off.
- **Replay controls:** play and pause, 1× to 30× speed, ±30 s, and jump to any lap.

### Live mode

OpenF1's free tier is historical only. **While any session is live, OpenF1 closes the free
API completely, including historical data.** The app then serves whatever it has already
cached and shows a notice for anything else.

Live data needs an OpenF1 sponsor account (€9.90/month). Set your credentials as
environment variables before starting the app:

```bash
export OPENF1_USERNAME='you@example.com'
export OPENF1_PASSWORD='...'
uv run streamlit run app.py
```

Or set `OPENF1_ACCESS_TOKEN` to a token you already have. In live mode the app polls every
3 seconds: car positions on every poll, gaps and positions every 6 seconds, and laps, tyres,
pit stops and race control every 30 seconds. That is about 48 requests per minute, inside the
sponsor tier's 60. The map runs 5 seconds behind the feed, so there is always data to
animate towards and cars keep moving between polls.

The live polling logic is tested by simulating a finished race. The login step itself
has not been tested against a real sponsor account yet.

## How the analysis works

| Step | Method |
|---|---|
| Clean laps | Timed laps that are not out-laps or in-laps (from OpenF1 `pit` data) |
| Push laps | Laps within 4% of the stint's fastest clean lap |
| Long runs | 5+ push laps in a row (one traffic lap allowed) that make up at least 75% of the run's laps. This separates race simulations from qualifying runs, which alternate push and cool-down laps |
| Fuel correction | Adds back 0.05 s per lap of fuel burn, so the remaining slope is tyre wear |
| Degradation | Linear fit of fuel-corrected lap time against tyre age. The field-wide value pools all runs after removing each run's average |
| Sector colours | Compared with every sector completed up to that moment, so the replay colours exactly what the live screen showed |

The assumptions are sliders in the sidebar. Teams do not publish fuel loads or engine
modes, so practice pace is an estimate.

## Project layout

```
app.py                Navigation between the two pages
views/weekend.py      Weekend analysis page
views/tracker.py      Race tracker page (replay and live)
views/common.py       Cached loaders, Grand Prix and driver pickers
views/track_map.py    Animated track map (custom component, frontend in track_map_frontend/)
pacelab/client.py     OpenF1 client: SQLite cache, 3/s + 30/min rate limit, sponsor login
pacelab/data.py       Loads sessions; joins laps with stints, pit stops and drivers
pacelab/analysis.py   Short-run, long-run, sector and degradation analysis
pacelab/race.py       Race state at any moment, track outline, car positions, live feed
pacelab/ui.py         HTML timing tables, tyre icons, team badges, flag banner
pacelab/teams.py      Team codes and colour helpers
tests/                Unit tests on synthetic data
```

## Roadmap

- Compare practice predictions with qualifying and race results automatically
- Undercut and overcut simulator
- Deploy to Streamlit Community Cloud

Data: [OpenF1](https://openf1.org). This project is unofficial and is not associated
with Formula 1.
