"""Single-game prediction.

predict_game() turns two teams' rating snapshot + situational factors into a
win probability and a predicted score. Every factor is returned as a named
line item (in Elo points and scoreboard points) so the UI can show *why* the
model likes a team, and the what-if panel can override any of them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from app import config
from app.model.ratings import RatingSnapshot, TeamHistory, expected_score


@dataclass
class Overrides:
    """User-adjustable inputs. None means 'use the model default'."""
    home_qb_out: bool | None = None
    away_qb_out: bool | None = None
    qb_penalty: float | None = None          # Elo points
    neutral_site: bool | None = None
    home_rest: int | None = None
    away_rest: int | None = None
    home_extra: float = 0.0                  # free-form Elo adjustment (e.g. other injuries)
    away_extra: float = 0.0

    @classmethod
    def from_dict(cls, d: dict | None) -> "Overrides":
        d = d or {}
        def num(v, cast, lo, hi):
            if v is None or v == "":
                return None
            try:
                x = cast(v)
            except (TypeError, ValueError):
                return None
            return max(lo, min(hi, x))
        return cls(
            home_qb_out=None if d.get("home_qb_out") is None else bool(d["home_qb_out"]),
            away_qb_out=None if d.get("away_qb_out") is None else bool(d["away_qb_out"]),
            qb_penalty=num(d.get("qb_penalty"), float, 0, 200),
            neutral_site=None if d.get("neutral_site") is None else bool(d["neutral_site"]),
            home_rest=num(d.get("home_rest"), int, 3, 21),
            away_rest=num(d.get("away_rest"), int, 3, 21),
            home_extra=num(d.get("home_extra"), float, -150, 150) or 0.0,
            away_extra=num(d.get("away_extra"), float, -150, 150) or 0.0,
        )


@dataclass
class Factor:
    key: str
    label: str
    elo: float                 # positive favours the home team
    note: str = ""

    @property
    def points(self) -> float:
        return self.elo / config.ELO_POINTS_PER_POINT


@dataclass
class Prediction:
    home_team: str
    away_team: str
    home_win_prob: float
    predicted_winner: str
    home_score: int
    away_score: int
    margin: float               # expected home margin (points, signed)
    total_elo_diff: float
    factors: list[Factor] = field(default_factory=list)
    home_elo: float = 0.0
    away_elo: float = 0.0
    inputs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["away_win_prob"] = round(1 - self.home_win_prob, 4)
        d["home_win_prob"] = round(self.home_win_prob, 4)
        d["margin"] = round(self.margin, 1)
        d["total_elo_diff"] = round(self.total_elo_diff, 1)
        d["home_elo"] = round(self.home_elo, 1)
        d["away_elo"] = round(self.away_elo, 1)
        d["factors"] = [{**asdict(f), "elo": round(f.elo, 1), "points": round(f.points, 1)} for f in self.factors]
        return d


def rest_adjustment(rest_days: int) -> float:
    raw = (rest_days - 7) * config.REST_POINTS_PER_DAY
    return max(config.REST_ADJ_MIN, min(config.REST_ADJ_MAX, raw))


def form_adjustment(history: TeamHistory | None) -> float:
    if history is None:
        return 0.0
    raw = history.avg_margin(config.FORM_GAMES) * config.FORM_POINTS_PER_MARGIN
    return max(-config.FORM_ADJ_MAX, min(config.FORM_ADJ_MAX, raw))


def expected_points(off: TeamHistory | None, deff: TeamHistory | None) -> float:
    """Expected points for a team: blend of what it usually scores and what
    its opponent usually allows."""
    pf = off.avg_points_for() if off else config.LEAGUE_AVG_POINTS
    pa = deff.avg_points_against() if deff else config.LEAGUE_AVG_POINTS
    return (pf + pa) / 2.0


def split_score(total: float, margin: float, home_team: str, away_team: str) -> tuple[int, int]:
    """Turn an expected total and home margin into integer scores. Ties are
    broken toward the favourite so the score always agrees with the pick."""
    home = round((total + margin) / 2)
    away = round((total - margin) / 2)
    home = max(0, home)
    away = max(0, away)
    if home == away:
        if margin >= 0:
            home += 1
        else:
            away += 1
    return int(home), int(away)


def predict_game(
    snap: RatingSnapshot,
    home_team: str,
    away_team: str,
    *,
    neutral_site: bool = False,
    home_rest: int = 7,
    away_rest: int = 7,
    home_qb_out: bool = False,
    away_qb_out: bool = False,
    overrides: Overrides | None = None,
) -> Prediction:
    ov = overrides or Overrides()
    neutral = ov.neutral_site if ov.neutral_site is not None else neutral_site
    h_rest = ov.home_rest if ov.home_rest is not None else home_rest
    a_rest = ov.away_rest if ov.away_rest is not None else away_rest
    h_qb_out = ov.home_qb_out if ov.home_qb_out is not None else home_qb_out
    a_qb_out = ov.away_qb_out if ov.away_qb_out is not None else away_qb_out
    qb_pen = ov.qb_penalty if ov.qb_penalty is not None else config.QB_OUT_PENALTY

    home_elo = snap.elo.get(home_team, config.ELO_START)
    away_elo = snap.elo.get(away_team, config.ELO_START)
    h_hist = snap.history.get(home_team)
    a_hist = snap.history.get(away_team)

    factors: list[Factor] = [
        Factor("elo", "Team strength (Elo)", home_elo - away_elo,
               f"{home_team} {home_elo:.0f} vs {away_team} {away_elo:.0f}"),
        Factor("home", "Home-field advantage", 0.0 if neutral else config.ELO_HOME_ADVANTAGE,
               "Neutral site" if neutral else f"{home_team} at home"),
        Factor("rest", "Rest days", rest_adjustment(h_rest) - rest_adjustment(a_rest),
               f"{home_team} {h_rest}d vs {away_team} {a_rest}d"),
        Factor("form", "Recent form", form_adjustment(h_hist) - form_adjustment(a_hist),
               f"avg margin last {config.FORM_GAMES}: {home_team} {h_hist.avg_margin() if h_hist else 0:+.1f}, "
               f"{away_team} {a_hist.avg_margin() if a_hist else 0:+.1f}"),
        Factor("qb", "Starting QB availability", (-qb_pen if h_qb_out else 0.0) + (qb_pen if a_qb_out else 0.0),
               ", ".join(x for x in [f"{home_team} QB out" if h_qb_out else "",
                                     f"{away_team} QB out" if a_qb_out else ""] if x) or "Both starters expected"),
    ]
    if ov.home_extra or ov.away_extra:
        factors.append(Factor("manual", "Your manual adjustment", ov.home_extra - ov.away_extra,
                              f"{home_team} {ov.home_extra:+.0f}, {away_team} {ov.away_extra:+.0f}"))

    diff = sum(f.elo for f in factors)
    prob = expected_score(diff, 0.0)
    margin = diff / config.ELO_POINTS_PER_POINT
    total = expected_points(h_hist, a_hist) + expected_points(a_hist, h_hist)
    hs, as_ = split_score(total, margin, home_team, away_team)
    winner = home_team if prob >= 0.5 else away_team

    return Prediction(
        home_team=home_team,
        away_team=away_team,
        home_win_prob=prob,
        predicted_winner=winner,
        home_score=hs,
        away_score=as_,
        margin=margin,
        total_elo_diff=diff,
        factors=factors,
        home_elo=home_elo,
        away_elo=away_elo,
        inputs={
            "neutral_site": neutral, "home_rest": h_rest, "away_rest": a_rest,
            "home_qb_out": h_qb_out, "away_qb_out": a_qb_out, "qb_penalty": qb_pen,
            "home_extra": ov.home_extra, "away_extra": ov.away_extra,
            "model_version": config.MODEL_VERSION,
        },
    )
