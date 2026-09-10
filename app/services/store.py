"""Application service: loads data, builds ratings, produces predictions and
keeps the SQLite tables in sync. One instance lives for the process."""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timezone

from app import config, db
from app.data import sources
from app.data.sources import Game, Injury, LiveStatus
from app.data.teams import TEAMS, get_team
from app.model.predictor import Overrides, Prediction, predict_game
from app.model.ratings import RatingEngine
from app.services import scoring

log = logging.getLogger(__name__)


class NotFound(Exception):
    pass


class GameLocked(Exception):
    pass


class DataStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.games: list[Game] = []
        self.games_by_id: dict[str, Game] = {}
        self.season: int = 0
        self.engine: RatingEngine | None = None
        self.injuries: list[Injury] = []
        self.injuries_by_team: dict[str, list[Injury]] = defaultdict(list)
        self.source_info: dict = {}
        self.loaded_at: datetime | None = None
        self._live_cache: dict[tuple[int, int], tuple[float, dict[str, LiveStatus]]] = {}

    # ------------------------------------------------------------------ load
    def load(self, force: bool = False) -> None:
        with self._lock:
            if force:
                for f in config.DATA_CACHE_DIR.glob("*.json"):
                    f.unlink(missing_ok=True)
                (config.DATA_CACHE_DIR / "games.csv").unlink(missing_ok=True)
                self._live_cache.clear()
            games, info = sources.load_games()
            self.games = games
            self.games_by_id = {g.game_id: g for g in games}
            self.season = max(g.season for g in games)
            self.engine = RatingEngine(games)
            self.source_info = info
            try:
                self.injuries = sources.fetch_injuries()
            except Exception as exc:  # network layer already guards; belt and braces
                log.warning("injuries unavailable: %s", exc)
                self.injuries = []
            self.injuries_by_team = defaultdict(list)
            for inj in self.injuries:
                self.injuries_by_team[inj.team].append(inj)
            self.loaded_at = datetime.now(timezone.utc)
            db.init_db()
            log.info("loaded %d games; season %s; %d injuries; source=%s",
                     len(games), self.season, len(self.injuries), info.get("origin"))

    # --------------------------------------------------------------- helpers
    def season_games(self, season: int | None = None, reg_only: bool = True) -> list[Game]:
        season = season or self.season
        return [g for g in self.games if g.season == season and (g.game_type == "REG" or not reg_only)]

    def weeks(self) -> list[int]:
        return sorted({g.week for g in self.season_games()})

    def current_week(self, today: date | None = None) -> int:
        """First regular-season week whose last game day is today or later."""
        today = today or datetime.now(timezone.utc).date()
        by_week: dict[int, date] = {}
        for g in self.season_games():
            by_week[g.week] = max(by_week.get(g.week, g.gameday), g.gameday)
        for w in sorted(by_week):
            if by_week[w] >= today:
                return w
        return max(by_week) if by_week else 1

    def get_game(self, game_id: str) -> Game:
        g = self.games_by_id.get(game_id)
        if not g:
            raise NotFound(game_id)
        return g

    def qb_flags(self, game: Game) -> dict:
        """Default QB-out flags from the ESPN injury report."""
        home = sources.qb_is_out(game.home_qb, self.injuries_by_team.get(game.home_team, []))
        away = sources.qb_is_out(game.away_qb, self.injuries_by_team.get(game.away_team, []))
        return {
            "home_qb_out": home is not None, "away_qb_out": away is not None,
            "home_qb_injury": home.to_dict() if home else None,
            "away_qb_injury": away.to_dict() if away else None,
        }

    # ------------------------------------------------------------ prediction
    def predict(self, game: Game, overrides: Overrides | None = None) -> Prediction:
        assert self.engine is not None
        snap = self.engine.snapshot_for(game.season, game.week)
        flags = self.qb_flags(game)
        return predict_game(
            snap, game.home_team, game.away_team,
            neutral_site=game.neutral_site, home_rest=game.home_rest, away_rest=game.away_rest,
            home_qb_out=flags["home_qb_out"], away_qb_out=flags["away_qb_out"], overrides=overrides,
        )

    def live_for_week(self, season: int, week: int) -> dict[str, LiveStatus]:
        key = (season, week)
        cached = self._live_cache.get(key)
        if cached and time.time() - cached[0] < config.ESPN_CACHE_TTL_SECONDS:
            return cached[1]
        live = sources.fetch_scoreboard(season, week)
        self._live_cache[key] = (time.time(), live)
        return live

    def _sync_week(self, conn, week: int) -> tuple[dict, dict, dict]:
        """Store predictions, lock started games, record results for a week.
        Returns (predictions, results, live) for that week."""
        now = datetime.now(timezone.utc)
        games = [g for g in self.season_games() if g.week == week]
        live = self.live_for_week(self.season, week)
        started: list[str] = []
        for g in games:
            has_started = g.kickoff_utc <= now
            ls = live.get(g.espn_id or "")
            if ls and ls.state in ("in", "post"):
                has_started = True
            pred = self.predict(g).to_dict()
            db.upsert_prediction(conn, g.game_id, g.season, g.week, g.home_team, g.away_team, pred,
                                 lock=has_started, retroactive=has_started)
            if has_started:
                started.append(g.game_id)
            if g.completed:
                db.upsert_result(conn, g.game_id, g.season, g.week, g.home_team, g.away_team,
                                 g.home_score, g.away_score, "nflverse")
            elif ls and ls.completed and ls.home_score is not None and ls.away_score is not None:
                db.upsert_result(conn, g.game_id, g.season, g.week, g.home_team, g.away_team,
                                 ls.home_score, ls.away_score, "espn")
        db.lock_started(conn, started)
        return db.get_predictions(conn, self.season, week), db.get_results(conn, self.season, week), live

    def _game_view(self, g: Game, pred_row: dict | None, result: dict | None, pick: dict | None,
                   ls: LiveStatus | None) -> dict:
        home, away = TEAMS[g.home_team], TEAMS[g.away_team]
        pred = self.predict(g).to_dict()
        stored = None
        if pred_row:
            stored = {k: pred_row[k] for k in ("home_win_prob", "predicted_winner", "home_score", "away_score",
                                                "locked", "retroactive", "model_version")}
        now = datetime.now(timezone.utc)
        state = "pre"
        detail = ""
        if result:
            state = "post"
        elif ls:
            state = ls.state
            detail = ls.detail
        elif g.kickoff_utc <= now:
            state = "in"
        return {
            "game": g.to_dict(),
            "home": home.to_dict(),
            "away": away.to_dict(),
            "prediction": pred,
            "stored_prediction": stored,
            "qb": self.qb_flags(g),
            "result": result,
            "pick": pick,
            "live": {"state": state, "detail": detail,
                     "home_score": ls.home_score if ls else None,
                     "away_score": ls.away_score if ls else None},
            "locked": state != "pre",
        }

    # ------------------------------------------------------------- payloads
    def week_payload(self, week: int) -> dict:
        with self._lock:
            games = [g for g in self.season_games() if g.week == week]
            if not games:
                raise NotFound(f"week {week}")
            with db.connect() as conn:
                preds, results, live = self._sync_week(conn, week)
                picks = db.get_picks(conn, self.season, week)
            views = [self._game_view(g, preds.get(g.game_id), results.get(g.game_id), picks.get(g.game_id),
                                     live.get(g.espn_id or "")) for g in games]
            return {"season": self.season, "week": week, "games": views}

    def game_payload(self, game_id: str) -> dict:
        with self._lock:
            g = self.get_game(game_id)
            with db.connect() as conn:
                preds, results, live = self._sync_week(conn, g.week)
                picks = db.get_picks(conn, self.season, g.week)
            view = self._game_view(g, preds.get(g.game_id), results.get(g.game_id), picks.get(g.game_id),
                                   live.get(g.espn_id or ""))
            view["injuries"] = {
                "home": [i.to_dict() for i in self.injuries_by_team.get(g.home_team, [])],
                "away": [i.to_dict() for i in self.injuries_by_team.get(g.away_team, [])],
            }
            return view

    def what_if(self, game_id: str, overrides: dict | None) -> dict:
        g = self.get_game(game_id)
        return self.predict(g, Overrides.from_dict(overrides)).to_dict()

    def teams_payload(self) -> list[dict]:
        assert self.engine is not None
        snap = self.engine.snapshot_for(self.season, self.current_week())
        ranked = {t: i + 1 for i, (t, _) in enumerate(self.engine.rankings(snap))}
        out = []
        for abbr, team in TEAMS.items():
            rec = snap.season_records.get(abbr, (0, 0, 0))
            out.append({**team.to_dict(), "elo": round(snap.elo.get(abbr, config.ELO_START), 1),
                        "rank": ranked.get(abbr, 32), "record": f"{rec[0]}-{rec[1]}" + (f"-{rec[2]}" if rec[2] else "")})
        out.sort(key=lambda t: t["rank"])
        return out

    def team_payload(self, abbr: str) -> dict:
        team = get_team(abbr)
        if not team:
            raise NotFound(abbr)
        assert self.engine is not None
        with self._lock:
            cur = self.current_week()
            snap = self.engine.snapshot_for(self.season, cur)
            hist = snap.history.get(team.abbr)
            with db.connect() as conn:
                picks = db.get_picks(conn, self.season)
                results = db.get_results(conn, self.season)
            schedule = []
            for g in self.season_games():
                if team.abbr not in (g.home_team, g.away_team):
                    continue
                opp = g.away_team if g.home_team == team.abbr else g.home_team
                pred = self.predict(g).to_dict()
                team_prob = pred["home_win_prob"] if g.home_team == team.abbr else pred["away_win_prob"]
                res = results.get(g.game_id)
                if res is None and g.completed:
                    res = {"home_score": g.home_score, "away_score": g.away_score, "winner": g.winner}
                schedule.append({
                    "game": g.to_dict(), "opponent": TEAMS[opp].to_dict(), "is_home": g.home_team == team.abbr,
                    "team_win_prob": round(team_prob, 4), "prediction": pred, "result": res,
                    "pick": picks.get(g.game_id),
                })
            recent = []
            for g in reversed([g for g in self.games if team.abbr in (g.home_team, g.away_team) and g.completed]):
                if len(recent) >= 8:
                    break
                is_home = g.home_team == team.abbr
                pf, pa = (g.home_score, g.away_score) if is_home else (g.away_score, g.home_score)
                recent.append({"game_id": g.game_id, "season": g.season, "week": g.week, "game_type": g.game_type,
                               "opponent": g.away_team if is_home else g.home_team, "is_home": is_home,
                               "pf": pf, "pa": pa, "won": pf > pa})
            rec = snap.season_records.get(team.abbr, (0, 0, 0))
            ranked = {t: i + 1 for i, (t, _) in enumerate(self.engine.rankings(snap))}
            return {
                **team.to_dict(),
                "elo": round(snap.elo.get(team.abbr, config.ELO_START), 1),
                "rank": ranked.get(team.abbr, 32),
                "record": {"wins": rec[0], "losses": rec[1], "ties": rec[2]},
                "avg_points_for": round(hist.avg_points_for(), 1) if hist else None,
                "avg_points_against": round(hist.avg_points_against(), 1) if hist else None,
                "form_margin": round(hist.avg_margin(), 1) if hist else None,
                "injuries": [i.to_dict() for i in self.injuries_by_team.get(team.abbr, [])],
                "schedule": schedule,
                "recent": recent,
            }

    # ----------------------------------------------------------------- picks
    def submit_pick(self, game_id: str, picked_team: str, home_score=None, away_score=None) -> dict:
        g = self.get_game(game_id)
        team = get_team(picked_team)
        if not team or team.abbr not in (g.home_team, g.away_team):
            raise ValueError("picked_team must be one of the two teams in the game")
        if g.completed or g.kickoff_utc <= datetime.now(timezone.utc):
            raise GameLocked(game_id)
        def clean(v):
            if v is None or v == "":
                return None
            v = int(v)
            if v < 0 or v > 99:
                raise ValueError("scores must be between 0 and 99")
            return v
        with db.connect() as conn:
            return db.upsert_pick(conn, g.game_id, g.season, g.week, team.abbr, clean(home_score), clean(away_score))

    def remove_pick(self, game_id: str) -> bool:
        g = self.get_game(game_id)
        if g.completed or g.kickoff_utc <= datetime.now(timezone.utc):
            raise GameLocked(game_id)
        with db.connect() as conn:
            return db.delete_pick(conn, g.game_id)

    def picks_payload(self) -> dict:
        with self._lock:
            with db.connect() as conn:
                picks = db.get_picks(conn, self.season)
                preds = db.get_predictions(conn, self.season)
                results = db.get_results(conn, self.season)
            rows = []
            for gid, pick in picks.items():
                g = self.games_by_id.get(gid)
                if not g:
                    continue
                pred = preds.get(gid)
                res = results.get(gid)
                rows.append({
                    "game": g.to_dict(), "home": TEAMS[g.home_team].to_dict(), "away": TEAMS[g.away_team].to_dict(),
                    "pick": pick, "model_pick": pred["predicted_winner"] if pred else None,
                    "model_prob": pred["home_win_prob"] if pred else None, "result": res,
                    "agrees": (pred["predicted_winner"] == pick["picked_team"]) if pred else None,
                    "you_correct": (res["winner"] == pick["picked_team"]) if res else None,
                    "model_correct": (res["winner"] == pred["predicted_winner"]) if (res and pred) else None,
                })
            rows.sort(key=lambda r: (r["game"]["week"], r["game"]["gameday"], r["game"]["kickoff_utc"]))
            return {"season": self.season, "picks": rows, "agreement": scoring.agreement(preds, picks)}

    def leaderboard_payload(self) -> dict:
        with self._lock:
            # make sure every completed week's results are recorded before grading
            cur = self.current_week()
            with db.connect() as conn:
                for w in self.weeks():
                    if w <= cur:
                        self._sync_week(conn, w)
                picks = db.get_picks(conn, self.season)
                preds = db.get_predictions(conn, self.season)
                results = db.get_results(conn, self.season)
            meta = {g.game_id: {"home_team": g.home_team, "away_team": g.away_team, "week": g.week}
                    for g in self.season_games()}
            board = scoring.grade(preds, picks, results, meta)
            board.update({"season": self.season, "current_week": cur,
                          "agreement": scoring.agreement(preds, picks)})
            return board

    def meta_payload(self) -> dict:
        return {
            "season": self.season,
            "current_week": self.current_week(),
            "weeks": self.weeks(),
            "model_version": config.MODEL_VERSION,
            "data": {**self.source_info, "espn_live": not config.OFFLINE,
                     "injuries_count": len(self.injuries),
                     "loaded_at": self.loaded_at.isoformat() if self.loaded_at else None},
            "params": {
                "elo_home_advantage": config.ELO_HOME_ADVANTAGE, "elo_k": config.ELO_K,
                "elo_points_per_point": config.ELO_POINTS_PER_POINT, "season_regression": config.ELO_SEASON_REGRESSION,
                "rest_points_per_day": config.REST_POINTS_PER_DAY, "rest_min": config.REST_ADJ_MIN,
                "rest_max": config.REST_ADJ_MAX, "form_games": config.FORM_GAMES,
                "form_points_per_margin": config.FORM_POINTS_PER_MARGIN, "form_max": config.FORM_ADJ_MAX,
                "qb_out_penalty": config.QB_OUT_PENALTY, "scoring_games": config.SCORING_GAMES,
            },
        }


store = DataStore()
