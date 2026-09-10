"""SQLite persistence (stdlib only).

Tables
------
predictions  what the model said about each game, frozen at kickoff
picks        the user's own pick (and optional score) per game
results      final scores, merged from nflverse and ESPN
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    game_id        TEXT PRIMARY KEY,
    season         INTEGER NOT NULL,
    week           INTEGER NOT NULL,
    home_team      TEXT NOT NULL,
    away_team      TEXT NOT NULL,
    home_win_prob  REAL NOT NULL,
    predicted_winner TEXT NOT NULL,
    home_score     INTEGER NOT NULL,
    away_score     INTEGER NOT NULL,
    model_version  TEXT NOT NULL,
    inputs_json    TEXT NOT NULL,
    retroactive    INTEGER NOT NULL DEFAULT 0,
    locked         INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS picks (
    game_id        TEXT PRIMARY KEY,
    season         INTEGER NOT NULL,
    week           INTEGER NOT NULL,
    picked_team    TEXT NOT NULL,
    home_score     INTEGER,
    away_score     INTEGER,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
    game_id        TEXT PRIMARY KEY,
    season         INTEGER NOT NULL,
    week           INTEGER NOT NULL,
    home_team      TEXT NOT NULL,
    away_team      TEXT NOT NULL,
    home_score     INTEGER NOT NULL,
    away_score     INTEGER NOT NULL,
    winner         TEXT NOT NULL,
    source         TEXT NOT NULL,
    recorded_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pred_week ON predictions(season, week);
CREATE INDEX IF NOT EXISTS idx_picks_week ON picks(season, week);
CREATE INDEX IF NOT EXISTS idx_results_week ON results(season, week);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(path=None) -> None:
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(path=None) -> Iterator[sqlite3.Connection]:
    path = path or config.DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---- predictions -----------------------------------------------------------
def upsert_prediction(conn: sqlite3.Connection, game_id: str, season: int, week: int,
                      home_team: str, away_team: str, pred: dict, *, lock: bool, retroactive: bool) -> bool:
    """Store the model's prediction. Once a row is locked (game has kicked
    off) it is never overwritten. Returns True if a write happened."""
    row = conn.execute("SELECT locked FROM predictions WHERE game_id=?", (game_id,)).fetchone()
    if row and row["locked"]:
        return False
    now = _now()
    conn.execute(
        """INSERT INTO predictions (game_id, season, week, home_team, away_team, home_win_prob,
              predicted_winner, home_score, away_score, model_version, inputs_json, retroactive, locked,
              created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id) DO UPDATE SET
              home_win_prob=excluded.home_win_prob, predicted_winner=excluded.predicted_winner,
              home_score=excluded.home_score, away_score=excluded.away_score,
              model_version=excluded.model_version, inputs_json=excluded.inputs_json,
              retroactive=excluded.retroactive, locked=excluded.locked, updated_at=excluded.updated_at""",
        (game_id, season, week, home_team, away_team, pred["home_win_prob"], pred["predicted_winner"],
         pred["home_score"], pred["away_score"], pred["inputs"].get("model_version", config.MODEL_VERSION),
         json.dumps(pred["inputs"]), int(retroactive), int(lock), now, now),
    )
    return True


def get_predictions(conn: sqlite3.Connection, season: int, week: int | None = None) -> dict[str, dict]:
    q = "SELECT * FROM predictions WHERE season=?"
    args: list = [season]
    if week is not None:
        q += " AND week=?"
        args.append(week)
    return {r["game_id"]: dict(r) for r in conn.execute(q, args)}


# ---- picks -----------------------------------------------------------------
def upsert_pick(conn: sqlite3.Connection, game_id: str, season: int, week: int, picked_team: str,
                home_score: int | None, away_score: int | None) -> dict:
    now = _now()
    conn.execute(
        """INSERT INTO picks (game_id, season, week, picked_team, home_score, away_score, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id) DO UPDATE SET picked_team=excluded.picked_team,
              home_score=excluded.home_score, away_score=excluded.away_score, updated_at=excluded.updated_at""",
        (game_id, season, week, picked_team, home_score, away_score, now, now),
    )
    return dict(conn.execute("SELECT * FROM picks WHERE game_id=?", (game_id,)).fetchone())


def delete_pick(conn: sqlite3.Connection, game_id: str) -> bool:
    cur = conn.execute("DELETE FROM picks WHERE game_id=?", (game_id,))
    return cur.rowcount > 0


def get_picks(conn: sqlite3.Connection, season: int, week: int | None = None) -> dict[str, dict]:
    q = "SELECT * FROM picks WHERE season=?"
    args: list = [season]
    if week is not None:
        q += " AND week=?"
        args.append(week)
    return {r["game_id"]: dict(r) for r in conn.execute(q, args)}


# ---- results ---------------------------------------------------------------
def upsert_result(conn: sqlite3.Connection, game_id: str, season: int, week: int, home_team: str,
                  away_team: str, home_score: int, away_score: int, source: str) -> None:
    winner = home_team if home_score > away_score else away_team if away_score > home_score else "TIE"
    conn.execute(
        """INSERT INTO results (game_id, season, week, home_team, away_team, home_score, away_score,
              winner, source, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id) DO UPDATE SET home_score=excluded.home_score, away_score=excluded.away_score,
              winner=excluded.winner, source=excluded.source, recorded_at=excluded.recorded_at""",
        (game_id, season, week, home_team, away_team, home_score, away_score, winner, source, _now()),
    )


def get_results(conn: sqlite3.Connection, season: int, week: int | None = None) -> dict[str, dict]:
    q = "SELECT * FROM results WHERE season=?"
    args: list = [season]
    if week is not None:
        q += " AND week=?"
        args.append(week)
    return {r["game_id"]: dict(r) for r in conn.execute(q, args)}


def lock_started(conn: sqlite3.Connection, game_ids: list[str]) -> None:
    if not game_ids:
        return
    marks = ",".join("?" * len(game_ids))
    conn.execute(f"UPDATE predictions SET locked=1 WHERE game_id IN ({marks})", game_ids)
