# NFL Predictor

A local, single-user web app that predicts every game of the 2026 NFL season with a transparent
Elo-based model, lets you play "what if" with the inputs, records your own picks, and grades both
you and the model on a leaderboard once real results come in.

> Predictions are a simple statistical model for entertainment and learning. This is **not**
> betting advice and no claim is made about accuracy against betting markets.

## Quick start

Requires Python 3.11+ (developed on 3.13). No Node, no database server, no API keys.

```bash
./run.sh
```

That creates a virtualenv on first run, installs four packages, and serves the app at
<http://127.0.0.1:8000>. Or do it by hand:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn app.main:app --reload
```

Run the tests:

```bash
./.venv/bin/python -m pytest
```

Useful environment variables:

| Variable | Effect |
| --- | --- |
| `NFL_OFFLINE=1` | Never touch the network; use the bundled data snapshot only |
| `NFL_DATA_CACHE=path` | Where downloaded files and the SQLite DB live (default `./data_cache`) |
| `NFL_DB_PATH=path` | SQLite file location (default inside the cache dir) |
| `PORT=9000` | Port for `run.sh` |

## What it does

* **Schedule view** — every week of the regular season, grouped by day, with the model's pick,
  win probability (as a two-colour bar), predicted score, live/final scores, and one-tap picks.
* **Game page** — a line-by-line breakdown of *why* the model favours a team, a what-if panel
  (starting QB out, QB impact size, neutral site, rest days, free-form adjustments) that re-runs the
  model instantly, the ESPN injury report for both teams, and your pick with an optional score.
* **Teams** — power rankings for all 32 teams and a team page with rating, record, scoring
  profile, recent form, injuries and the full season schedule with predictions.
* **My Picks** — every pick you've made next to the model's, with agreement stats and results.
* **Leaderboard** — accuracy, Brier score and score error for *you*, *the model* and an
  *always-pick-home* baseline, plus a week-by-week table.
* **How it works** — the methodology page, driven by the same config values the model uses.

## Architecture

Single Python process: FastAPI serves a JSON API and a static, framework-free single-page frontend.

```
app/
  config.py            all tunable model parameters and paths
  data/
    teams.py           static reference for the 32 teams (abbr mapping, colours, ESPN ids)
    sources.py         nflverse + ESPN fetchers, parsers, disk cache, offline fallback
    snapshot/          bundled copy of nflverse games.csv (2022-2026), the offline dataset
  model/
    ratings.py         Elo engine: replays history, exposes rating snapshots by (season, week)
    predictor.py       one-game prediction with named factors and user overrides
  services/
    store.py           orchestration: loads data, predicts, syncs SQLite, builds API payloads
    scoring.py         leaderboard grading (accuracy, Brier, score error)
  db.py                SQLite schema + queries (stdlib sqlite3, no ORM)
  main.py              FastAPI routes
static/                index.html, app.css, app.js (hash-routed SPA, no build step)
tests/                 pytest suites for the model, parsers, rating replay and API
```

**Why this stack.** The interesting part of the product is the model and the data plumbing, both
of which are most natural in Python. FastAPI gives typed request validation and auto-generated docs
(`/docs`) for almost no code. SQLite via the standard library means zero setup and a single file to
back up. A vanilla-JS frontend avoids a Node toolchain entirely, so "clone, run one script" is the
whole install; the UI is a few hundred lines and doesn't need a framework's state management.

**Separation of concerns.** `data/` knows about wire formats and nothing about football strategy;
`model/` is pure functions over dataclasses and has no I/O; `services/` is the only layer that
touches both the model and the database; `main.py` only maps HTTP to service calls.

### API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/meta` | season, current week, data source status, model parameters |
| GET | `/api/weeks/{week}` | games for a week with prediction, live status, result, your pick |
| GET | `/api/games/{id}` | one game with factor breakdown and injury report |
| POST | `/api/games/{id}/what-if` | re-run the model with `overrides` (not persisted) |
| GET | `/api/teams`, `/api/teams/{abbr}` | power ratings; team page payload |
| GET/PUT/DELETE | `/api/picks`, `/api/picks/{id}` | your picks (locked at kickoff → 409) |
| GET | `/api/leaderboard` | graded results |
| POST | `/api/refresh` | re-download schedule, scores and injuries |

## How the prediction model works

Five explainable factors are added into a single Elo-point edge `D` for the home team.

1. **Team strength (Elo).** Ratings start at 1500 and are replayed game by game from the 2022
   season, playoffs included. After each game `Δ = K · MOV · (actual − expected)` with `K = 20`;
   `expected` already includes home-field, so a favourite winning at home barely moves. The
   margin-of-victory multiplier `ln(|margin|+1) · 2.2 / (0.001·edge + 2.2)` rewards big wins with
   diminishing returns and discounts blowouts by heavy favourites (the FiveThirtyEight recipe).
   At every new season each team is regressed one-third of the way back to 1500.
2. **Home field.** +48 Elo (≈ 2 points) to the home team, 0 at neutral sites (the nflverse
   schedule marks international and other neutral games).
3. **Rest.** 4 Elo per rest day above or below the normal 7, clamped to [−20, +25]. A bye is worth
   about a point; a Thursday game after a Sunday game costs about half a point.
4. **Recent form.** 2.5 Elo per point of average margin over the last 5 games, capped at ±30.
   This deliberately double-counts a little on top of Elo to make the model quicker to react to
   hot or cold streaks.
5. **Starting quarterback.** −70 Elo (≈ 2.8 points) when the team's projected starter (from the
   nflverse schedule) is listed as Out, Doubtful, IR or suspended on ESPN's injury report. Other
   injuries are *not* automatically modelled; the what-if panel has a manual slider for them.

Then `P(home) = 1 / (1 + 10^(−D/400))` and `expected margin = D / 25`. The expected total comes from
each team's points scored and allowed over its last 17 games (offence vs. the opponent's defence,
averaged); the margin is split around that total, rounded, and a rounding tie is broken toward the
favourite so the score always agrees with the pick.

All constants live in `app/config.py` and are shown on the in-app methodology page.

### Prediction integrity

When a week is viewed, the model's prediction for each game is upserted into SQLite. At kickoff
(by schedule time, or as soon as ESPN reports the game live) the row is **locked** and never
overwritten, so the leaderboard grades what the model actually said beforehand. If the app first
sees a game after it has started, the prediction is stored with a `retroactive` flag and labelled
as such on the leaderboard. User picks are rejected with HTTP 409 after kickoff. What-if scenarios
are never stored.

## Data sources

| Source | Used for | Notes |
| --- | --- | --- |
| [nflverse `games.csv`](https://github.com/nflverse/nfldata/blob/master/data/games.csv) | schedule, final scores, rest days, projected starting QBs, neutral-site flag, ESPN event ids | Public CSV on GitHub, updated by the nflverse maintainers within about a day of games. Downloaded on startup, cached on disk for 6 hours. |
| [ESPN site API](https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard) (`/scoreboard`, `/injuries`) | live game state and scores; league-wide injury report | Public and unauthenticated but undocumented, so treated as best-effort: any failure just means no live scores/injuries. Cached for 5 minutes. ESPN returns 403 to browser-like User-Agents from scripts, so requests go out with Python's default identity. |
| `app/data/snapshot/games_2022_2026.csv` | offline fallback | A trimmed copy of the nflverse file fetched on 2026-09-07 (2022–2025 results, full 2026 schedule). Used automatically if nflverse is unreachable or when `NFL_OFFLINE=1`. It is real data, not a mock. |

Team abbreviations follow nflverse (`LA`, `WAS`); the ESPN codes (`LAR`, `WSH`) and legacy codes
(`OAK`, `SD`, `STL`) are normalised in `app/data/teams.py`.

### Swapping in a different feed

Everything downstream consumes the `Game`, `LiveStatus` and `Injury` dataclasses in
`app/data/sources.py`. To use another provider, implement `load_games()`, `fetch_scoreboard()` and
`fetch_injuries()` returning those types (the parsers are separate functions so they can be
unit-tested against saved payloads, as in `tests/test_ratings_and_data.py`). Nothing in
`model/`, `services/` or the frontend needs to change. Results grading keys on `game_id`, so keep
nflverse-style ids (`2026_01_NE_SEA`) or migrate the `predictions`/`picks`/`results` tables.

## Known limitations

* Elo sees only final scores. It is slow to react to trades, coaching changes and rookie classes,
  and treats a 2025 playoff game and a 2026 opener as the same kind of evidence.
* Only quarterback availability is modelled automatically, and only by exact-ish name match
  between nflverse's projected starter and ESPN's injury list. A backup listed on IR is ignored;
  a starter listed "Questionable" is treated as playing.
* Predicted scores are expected values, so they land on "unusual" football scores more often than
  real games do. They are meant for comparing your own score picks, not as most-likely outcomes.
* Kickoff times from nflverse are US Eastern and converted assuming daylight time, which holds for
  the entire regular season but would be an hour off for a January playoff game.
* The ESPN endpoints are undocumented and could change or start blocking without notice; the app
  degrades gracefully (no live scores, no injuries) but you'd lose those features.
* Single user, no auth: picks are stored in one local SQLite file.
* Models of this style historically pick the winner roughly 63–67% of the time.

## If I had more time

* **Better model:** add offensive/defensive EPA per play from nflverse play-by-play (nflfastR),
  QB-specific Elo adjustments like FiveThirtyEight's QB-adjusted Elo, weather for outdoor games, and
  a calibration step fitted on 2015–2025 with the same rest/home/form features.
* **Back-testing UI:** run the model over past seasons and show calibration curves and accuracy
  by week/spread bucket, so "how good is this?" is answered with data rather than a sentence.
* **Score distributions:** simulate games (e.g. Poisson/negative-binomial drives) to show a
  probability of each realistic final score instead of a single expected score.
* **Live updates:** background refresh on a timer and a websocket push so the schedule page ticks
  during Sunday games instead of relying on the 5-minute cache.
* **Multi-user picks:** trivial schema change (add a `user` column), then a real leaderboard among
  friends with optional login.
* **Season simulation:** Monte-Carlo the remaining schedule for playoff odds per team.
* **Packaging:** a Dockerfile and a GitHub Action running the tests and a smoke test of both
  data sources.
