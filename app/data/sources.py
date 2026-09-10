"""Data access layer.

Two real public sources are used:

* nflverse `games.csv` (https://github.com/nflverse/nfldata) — the schedule,
  final scores, rest days, projected starting QBs and ESPN event ids for every
  game since 1999. Downloaded on demand and cached on disk; a trimmed
  snapshot (seasons 2022-2026, fetched 2026-09-07) is bundled as an offline
  fallback so the app always starts.
* ESPN's public site API (site.api.espn.com) — live game status/scores for a
  week and the league-wide injury report. Unofficial but stable and widely
  used; failures are non-fatal.

Everything returned from here is plain dataclasses; nothing downstream needs
to know the wire format.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from app import config
from app.data.teams import normalize_abbr, NAME_TO_ABBR

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------
@dataclass
class Game:
    game_id: str
    season: int
    game_type: str          # REG, WC, DIV, CON, SB
    week: int
    gameday: date
    gametime: str | None    # "HH:MM" US/Eastern, may be missing
    away_team: str
    home_team: str
    away_score: int | None
    home_score: int | None
    neutral_site: bool
    away_rest: int
    home_rest: int
    away_qb: str | None
    home_qb: str | None
    stadium: str | None
    roof: str | None
    espn_id: str | None

    @property
    def completed(self) -> bool:
        return self.home_score is not None and self.away_score is not None

    @property
    def winner(self) -> str | None:
        if not self.completed:
            return None
        if self.home_score > self.away_score:
            return self.home_team
        if self.away_score > self.home_score:
            return self.away_team
        return "TIE"

    @property
    def kickoff_utc(self) -> datetime:
        """Best-effort kickoff in UTC. nflverse times are US/Eastern; we treat
        them as UTC-4 (EDT) which is correct for the entire regular season."""
        hh, mm = 13, 0
        if self.gametime and re.match(r"^\d{1,2}:\d{2}$", self.gametime):
            hh, mm = (int(x) for x in self.gametime.split(":"))
        local = datetime(self.gameday.year, self.gameday.month, self.gameday.day, hh, mm)
        return (local + timedelta(hours=4)).replace(tzinfo=timezone.utc)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["gameday"] = self.gameday.isoformat()
        d["kickoff_utc"] = self.kickoff_utc.isoformat()
        d["completed"] = self.completed
        d["winner"] = self.winner
        return d


@dataclass
class LiveStatus:
    espn_id: str
    state: str              # pre | in | post
    completed: bool
    detail: str
    home_abbr: str | None
    away_abbr: str | None
    home_score: int | None
    away_score: int | None


@dataclass
class Injury:
    team: str
    player: str
    position: str
    status: str
    detail: str
    comment: str

    def to_dict(self) -> dict:
        return asdict(self)


class DataError(Exception):
    """Raised when a source cannot be read or parsed at all."""


# ---------------------------------------------------------------------------
# Low-level HTTP with disk cache
# ---------------------------------------------------------------------------
def _http_get(url: str) -> bytes:
    # NOTE: ESPN's site API returns 403 for browser-like or custom User-Agents from
    # scripts; the stock urllib identity works, so only Accept is set here.
    req = urllib.request.Request(url, headers={"Accept": "application/json, text/csv, */*"})
    with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT_SECONDS) as resp:
        return resp.read()


def _cached_fetch(url: str, cache_file: Path, ttl: int) -> tuple[bytes | None, str]:
    """Return (bytes, origin). origin is 'cache', 'network' or 'none'."""
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < ttl:
            return cache_file.read_bytes(), "cache"
    if config.OFFLINE:
        return (cache_file.read_bytes(), "cache") if cache_file.exists() else (None, "none")
    try:
        data = _http_get(url)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(data)
        return data, "network"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("fetch failed for %s: %s", url, exc)
        if cache_file.exists():
            return cache_file.read_bytes(), "cache"
        return None, "none"


# ---------------------------------------------------------------------------
# nflverse games.csv
# ---------------------------------------------------------------------------
def _to_int(v: str | None) -> int | None:
    if v is None:
        return None
    v = v.strip()
    if v == "" or v.upper() == "NA":
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def parse_games_csv(text: str, min_season: int = 2022) -> list[Game]:
    """Parse nflverse games.csv content. Malformed rows are skipped and logged
    rather than crashing the whole load."""
    games: list[Game] = []
    reader = csv.DictReader(io.StringIO(text))
    required = {"game_id", "season", "week", "gameday", "away_team", "home_team"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise DataError(f"games.csv missing required columns; found {reader.fieldnames}")
    skipped = 0
    for row in reader:
        try:
            season = int(row["season"])
            if season < min_season:
                continue
            home = normalize_abbr(row["home_team"])
            away = normalize_abbr(row["away_team"])
            if not home or not away:
                raise ValueError(f"unknown team in {row.get('game_id')}")
            games.append(
                Game(
                    game_id=row["game_id"],
                    season=season,
                    game_type=(row.get("game_type") or "REG").upper(),
                    week=int(row["week"]),
                    gameday=date.fromisoformat(row["gameday"]),
                    gametime=(row.get("gametime") or None),
                    away_team=away,
                    home_team=home,
                    away_score=_to_int(row.get("away_score")),
                    home_score=_to_int(row.get("home_score")),
                    neutral_site=(row.get("location") or "Home").strip().lower() == "neutral",
                    away_rest=_to_int(row.get("away_rest")) or 7,
                    home_rest=_to_int(row.get("home_rest")) or 7,
                    away_qb=(row.get("away_qb_name") or None),
                    home_qb=(row.get("home_qb_name") or None),
                    stadium=(row.get("stadium") or None),
                    roof=(row.get("roof") or None),
                    espn_id=(row.get("espn") or None),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            skipped += 1
            log.debug("skipping malformed row %s: %s", row.get("game_id"), exc)
    if skipped:
        log.warning("skipped %d malformed rows in games.csv", skipped)
    if not games:
        raise DataError("games.csv parsed but produced no games")
    games.sort(key=lambda g: (g.gameday, g.gametime or "", g.game_id))
    return games


def load_games() -> tuple[list[Game], dict]:
    """Load the schedule+results. Returns (games, source_info)."""
    cache_file = config.DATA_CACHE_DIR / "games.csv"
    data, origin = _cached_fetch(config.NFLVERSE_GAMES_URL, cache_file, config.NFLVERSE_CACHE_TTL_SECONDS)
    info = {"source": "nflverse games.csv", "origin": origin, "url": config.NFLVERSE_GAMES_URL}
    if data:
        try:
            games = parse_games_csv(data.decode("utf-8"))
            info["fetched_at"] = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=timezone.utc).isoformat()
            return games, info
        except (DataError, UnicodeDecodeError) as exc:
            log.error("nflverse data unusable (%s); falling back to snapshot", exc)
    games = parse_games_csv(config.SNAPSHOT_PATH.read_text("utf-8"))
    info.update({"origin": "bundled snapshot", "fetched_at": "2026-09-07T00:00:00+00:00"})
    return games, info


# ---------------------------------------------------------------------------
# ESPN scoreboard (live status + scores for one week)
# ---------------------------------------------------------------------------
def parse_scoreboard(payload: dict) -> list[LiveStatus]:
    out: list[LiveStatus] = []
    for ev in payload.get("events", []) or []:
        try:
            comp = ev["competitions"][0]
            status = comp.get("status", {}).get("type", {})
            home = away = None
            hs = as_ = None
            for c in comp.get("competitors", []):
                abbr = normalize_abbr(c.get("team", {}).get("abbreviation"))
                score = _to_int(c.get("score"))
                if c.get("homeAway") == "home":
                    home, hs = abbr, score
                else:
                    away, as_ = abbr, score
            out.append(
                LiveStatus(
                    espn_id=str(ev["id"]),
                    state=status.get("state", "pre"),
                    completed=bool(status.get("completed", False)),
                    detail=status.get("shortDetail") or status.get("detail") or "",
                    home_abbr=home,
                    away_abbr=away,
                    home_score=hs,
                    away_score=as_,
                )
            )
        except (KeyError, IndexError, TypeError) as exc:
            log.debug("skipping malformed ESPN event: %s", exc)
    return out


def fetch_scoreboard(season: int, week: int) -> dict[str, LiveStatus]:
    url = f"{config.ESPN_SCOREBOARD_URL}?seasontype=2&week={week}&dates={season}"
    cache_file = config.DATA_CACHE_DIR / f"espn_scoreboard_{season}_w{week}.json"
    data, _ = _cached_fetch(url, cache_file, config.ESPN_CACHE_TTL_SECONDS)
    if not data:
        return {}
    try:
        return {s.espn_id: s for s in parse_scoreboard(json.loads(data))}
    except json.JSONDecodeError as exc:
        log.warning("ESPN scoreboard JSON invalid: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# ESPN injuries
# ---------------------------------------------------------------------------
def parse_injuries(payload: dict) -> list[Injury]:
    out: list[Injury] = []
    for team_block in payload.get("injuries", []) or []:
        team = NAME_TO_ABBR.get(team_block.get("displayName", ""))
        if not team:
            continue
        for inj in team_block.get("injuries", []) or []:
            try:
                ath = inj.get("athlete", {}) or {}
                out.append(
                    Injury(
                        team=team,
                        player=ath.get("displayName", "Unknown"),
                        position=(ath.get("position", {}) or {}).get("abbreviation", ""),
                        status=inj.get("status", "") or "",
                        detail=((inj.get("details", {}) or {}).get("type") or inj.get("type", {}).get("description") or ""),
                        comment=inj.get("shortComment", "") or "",
                    )
                )
            except (AttributeError, TypeError):
                continue
    return out


def fetch_injuries() -> list[Injury]:
    cache_file = config.DATA_CACHE_DIR / "espn_injuries.json"
    data, _ = _cached_fetch(config.ESPN_INJURIES_URL, cache_file, config.ESPN_CACHE_TTL_SECONDS)
    if not data:
        return []
    try:
        return parse_injuries(json.loads(data))
    except json.JSONDecodeError as exc:
        log.warning("ESPN injuries JSON invalid: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Helpers shared by services
# ---------------------------------------------------------------------------
_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)


def normalize_player_name(name: str | None) -> str:
    if not name:
        return ""
    n = _SUFFIXES.sub("", name.lower())
    return re.sub(r"[^a-z]", "", n)


def qb_is_out(qb_name: str | None, injuries: Iterable[Injury]) -> Injury | None:
    """Return the injury entry if the named QB is listed with an out-type status."""
    target = normalize_player_name(qb_name)
    if not target:
        return None
    for inj in injuries:
        if inj.position == "QB" and normalize_player_name(inj.player) == target:
            if inj.status.strip().lower() in config.INJURY_OUT_STATUSES:
                return inj
    return None
