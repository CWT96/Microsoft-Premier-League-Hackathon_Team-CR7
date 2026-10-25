# data/generator/

**Owners:** backend. Builds the synthetic match dataset in `data/source/`. Standard library only, Python 3.9+.

```bash
python -m data.generator.generate        # rewrite data/source/ (byte-identical every run)
python -m data.generator.check           # structure, the 3 must-haves, realism vs real data
python -m pytest data/generator/tests    # determinism + checks + "data/source matches the generator"
```

## How it works

| File | What |
|---|---|
| `rosters.py` | 4 fictional clubs: squads of 18, formations, style knobs (pass accuracy, directness, counter-attacking, pressing, fouling) |
| `matches.py` | The 3 matches: teams, seed, and the scripted story (goals, chances, red card, penalties, dull spells) |
| `engine.py` | Possession-by-possession simulator. Each touch is a carry, pass, dribble, shot, foul or loss of the ball, with probabilities set by the zone, the phase of play and the team's style. It writes events in Hudl/StatsBomb format |
| `generate.py` | Writes the Hudl folder layout plus `extras/` |
| `check.py` | Validation. Exit code 1 on any hard failure; realism gaps are printed as WARN |

**Scripted vs simulated.** Goals, red cards, penalties and some chances and dull spells come from the script in `matches.py`, so each match tells a known story and the scoreline is fixed. Everything between them is simulated. Shots that are not scripted never score.

**To change a match**, edit its scenes or seed in `matches.py`, then rerun `generate`, `check` and the tests. Never hand-edit `data/source/`: a test fails if it differs from the generator's output.

## Calibration
`check.py` compares every match with 7 real Hudl open-data matches (6 Premier League 2015/16 games and one Euro 2020 game). Only summary numbers are stored, in `REFERENCE`; no real data is committed. The comparison covers event counts per type, pass completion, possessions, half lengths and per-position stats.

It also measures the data the way the question bank (`backend/bank/`, `backend/resolve/`) sees it: only contract event types, per-90 touches, shots, saves and ball wins per position, and how instant questions settle inside slow spells. Player rates are hard checks (the questions are priced from real rates); instant-question rates are WARN only, because our dull spells deliberately stay in midfield. A test also checks the engine's average over 15 extra seeds is within 20% of real.
