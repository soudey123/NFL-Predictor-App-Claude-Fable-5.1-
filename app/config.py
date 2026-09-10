"""Central configuration. Everything tunable about the model lives here so the
"How this works" page and the tests reference the same numbers."""
from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_CACHE_DIR = Path(os.environ.get("NFL_DATA_CACHE", ROOT_DIR / "data_cache"))
DB_PATH = Path(os.environ.get("NFL_DB_PATH", DATA_CACHE_DIR / "nfl_predictor.db"))
SNAPSHOT_PATH = ROOT_DIR / "app" / "data" / "snapshot" / "games_2022_2026.csv"

# Remote sources (both are real, public, and unauthenticated).
NFLVERSE_GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"

# Set NFL_OFFLINE=1 to skip all network calls and use the bundled snapshot only.
OFFLINE = os.environ.get("NFL_OFFLINE", "0") == "1"
HTTP_TIMEOUT_SECONDS = 20
NFLVERSE_CACHE_TTL_SECONDS = 6 * 60 * 60      # schedule/results file re-download interval
ESPN_CACHE_TTL_SECONDS = 5 * 60              # live scores / injuries refresh interval

MODEL_VERSION = "elo-v1"

# ---- Model parameters -------------------------------------------------------
ELO_START = 1500.0            # every team starts here at the beginning of the history
ELO_K = 20.0                  # how fast ratings react to a single result
ELO_SEASON_REGRESSION = 1 / 3  # fraction of a team's rating moved back to 1500 each new season
ELO_HOME_ADVANTAGE = 48.0     # Elo points given to the home team (~2 points on the scoreboard)
ELO_POINTS_PER_POINT = 25.0   # 25 Elo points ~= 1 point of scoreboard margin

REST_POINTS_PER_DAY = 4.0     # Elo points per rest day above/below the normal 7
REST_ADJ_MIN = -20.0          # short-week floor (e.g. Thursday after Sunday)
REST_ADJ_MAX = 25.0           # bye-week ceiling

FORM_GAMES = 5                # games used for "recent form"
FORM_POINTS_PER_MARGIN = 2.5  # Elo points per point of average margin over FORM_GAMES
FORM_ADJ_MAX = 30.0           # cap in either direction

QB_OUT_PENALTY = 70.0         # Elo penalty when a team's expected starting QB is out (~2.8 points)
SCORING_GAMES = 17            # trailing games used to estimate points for/against
LEAGUE_AVG_POINTS = 22.5      # fallback per-team scoring average when history is thin

# ESPN injury statuses that mean the player will not play.
INJURY_OUT_STATUSES = {"out", "injured reserve", "doubtful", "suspension", "physically unable to perform"}
