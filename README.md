# F1 Pace Lab

Weekend pace analysis for Formula 1, built on [OpenF1](https://openf1.org) data.
After each practice session it answers the questions a race strategist asks on a
Friday night:

- **Who is quick over one lap?** Best clean lap, ideal lap from best sectors, speed
  traps, and the compound each time was set on.
- **Who is quick over a race distance?** Race simulations are detected automatically
  in the practice data, then fuel-corrected and ranked per team and compound.
- **How hard does each compound wear?** Degradation in seconds per lap, per run and
  pooled across the field.
- **Where is the time?** A sector-by-sector gap heatmap.

## Run it

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run streamlit run app.py
```

The app opens on the current Grand Prix. Pick the sessions to combine (FP1, FP2,
FP3, ...) in the sidebar. Use **Refresh data** after a session ends: OpenF1's free
tier publishes data shortly after each session.

Run the tests:

```bash
uv run pytest
```

## How the analysis works

All logic lives in `pacelab/analysis.py`; the assumptions are parameters you can
change in the app's sidebar.

| Step | Method |
|---|---|
| Clean laps | Timed laps that are not out-laps or in-laps (from OpenF1 `pit` data) |
| Short-run pace | Fastest clean lap per driver; ideal lap = sum of best sectors |
| Push laps | Laps within 4% of the stint's fastest clean lap |
| Long runs | 5+ push laps in a row (one traffic lap allowed), with at least 75% of the run's laps being push laps. This separates race simulations from qualifying runs, which alternate push and cool-down laps |
| Fuel correction | Adds back 0.05 s per lap of fuel burn, so the remaining slope is tyre wear |
| Degradation | Linear fit of fuel-corrected lap time against tyre age. The field-wide number pools all runs after removing each run's average, so faster cars do not bias the slope |

### Limitations

Teams do not publish fuel loads or engine modes, so practice pace is an estimate.
Compare long runs on the same compound, and treat one-lap order as a qualifying
prediction only for drivers who set their best on Softs.

## Project layout

```
pacelab/client.py    OpenF1 client: SQLite response cache, 3 req/s rate limit, 429 retry
pacelab/data.py      Loads sessions and joins laps with stints, pit stops and drivers
pacelab/analysis.py  Short-run, long-run and degradation analysis
app.py               Streamlit interface
tests/               Unit tests on synthetic lap data
```

## Roadmap

- Compare predictions with qualifying and race results automatically
- Race strategy simulator: pit windows, undercut and overcut, safety-car calls
- Live timing during sessions
- FastAPI and React front end

Data: [OpenF1](https://openf1.org). This project is unofficial and is not
associated with Formula 1.
