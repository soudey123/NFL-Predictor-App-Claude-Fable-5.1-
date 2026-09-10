"""FastAPI entry point. Run with:  uvicorn app.main:app --reload"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.services.store import GameLocked, NotFound, store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nfl")

STATIC_DIR = config.ROOT_DIR / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.load()
    yield


app = FastAPI(title="NFL Predictor", version="1.0.0", lifespan=lifespan)


class PickIn(BaseModel):
    team: str = Field(..., description="Abbreviation of the team you pick to win")
    home_score: int | None = Field(None, ge=0, le=99)
    away_score: int | None = Field(None, ge=0, le=99)


class WhatIfIn(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)


def _week_or_404(week: int):
    if week not in store.weeks():
        raise HTTPException(404, f"Week {week} is not in the {store.season} schedule")


# ------------------------------------------------------------------ API
@app.get("/api/meta")
def meta():
    return store.meta_payload()


@app.get("/api/teams")
def teams():
    return store.teams_payload()


@app.get("/api/teams/{abbr}")
def team(abbr: str):
    try:
        return store.team_payload(abbr)
    except NotFound:
        raise HTTPException(404, f"Unknown team {abbr}")


@app.get("/api/weeks/{week}")
def week(week: int):
    _week_or_404(week)
    return store.week_payload(week)


@app.get("/api/games/{game_id}")
def game(game_id: str):
    try:
        return store.game_payload(game_id)
    except NotFound:
        raise HTTPException(404, f"Unknown game {game_id}")


@app.post("/api/games/{game_id}/what-if")
def what_if(game_id: str, body: WhatIfIn):
    try:
        return store.what_if(game_id, body.overrides)
    except NotFound:
        raise HTTPException(404, f"Unknown game {game_id}")


@app.get("/api/picks")
def picks():
    return store.picks_payload()


@app.put("/api/picks/{game_id}")
def put_pick(game_id: str, body: PickIn):
    try:
        return store.submit_pick(game_id, body.team, body.home_score, body.away_score)
    except NotFound:
        raise HTTPException(404, f"Unknown game {game_id}")
    except GameLocked:
        raise HTTPException(409, "This game has already kicked off; picks are locked")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.delete("/api/picks/{game_id}")
def delete_pick(game_id: str):
    try:
        return {"deleted": store.remove_pick(game_id)}
    except NotFound:
        raise HTTPException(404, f"Unknown game {game_id}")
    except GameLocked:
        raise HTTPException(409, "This game has already kicked off; picks are locked")


@app.get("/api/leaderboard")
def leaderboard():
    return store.leaderboard_payload()


@app.post("/api/refresh")
def refresh():
    try:
        store.load(force=True)
    except Exception as exc:  # surfaced to the UI rather than crashing the server
        log.exception("refresh failed")
        raise HTTPException(503, f"Refresh failed: {exc}")
    return store.meta_payload()


@app.get("/api/health")
def health():
    return {"ok": True, "games": len(store.games), "season": store.season}


# --------------------------------------------------------------- frontend
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    return FileResponse(STATIC_DIR / "index.html")
