"""Team strength ratings built from game results.

The core is a margin-of-victory-aware Elo system (the same family of method
FiveThirtyEight popularised for the NFL). It is deliberately simple:

1. Every team starts at ELO_START at the beginning of the history window.
2. After each game the winner takes points from the loser. The amount is
   K * MOV multiplier * (actual - expected), where expected already accounts
   for home-field advantage, so a favourite winning at home moves little.
3. At the start of each new season a team's rating is pulled ELO_SEASON_REGRESSION
   of the way back toward the mean, reflecting roster turnover.

On top of Elo we also track, per team, a trailing window of results so the
predictor can compute "recent form" and expected points for/against.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

from app import config
from app.data.sources import Game


@dataclass
class TeamHistory:
    """Trailing results for one team, most recent last."""
    margins: deque = field(default_factory=lambda: deque(maxlen=config.SCORING_GAMES))
    points_for: deque = field(default_factory=lambda: deque(maxlen=config.SCORING_GAMES))
    points_against: deque = field(default_factory=lambda: deque(maxlen=config.SCORING_GAMES))
    wins: int = 0
    losses: int = 0
    ties: int = 0

    def record(self, pf: int, pa: int) -> None:
        self.points_for.append(pf)
        self.points_against.append(pa)
        self.margins.append(pf - pa)
        if pf > pa:
            self.wins += 1
        elif pa > pf:
            self.losses += 1
        else:
            self.ties += 1

    def avg_margin(self, n: int = config.FORM_GAMES) -> float:
        recent = list(self.margins)[-n:]
        return sum(recent) / len(recent) if recent else 0.0

    def avg_points_for(self) -> float:
        return sum(self.points_for) / len(self.points_for) if self.points_for else config.LEAGUE_AVG_POINTS

    def avg_points_against(self) -> float:
        return sum(self.points_against) / len(self.points_against) if self.points_against else config.LEAGUE_AVG_POINTS


@dataclass
class RatingSnapshot:
    """Ratings *before* the games of (season, week) are played."""
    season: int
    week: int
    elo: dict[str, float]
    history: dict[str, TeamHistory]
    season_records: dict[str, tuple[int, int, int]]   # W-L-T within `season` only


def expected_score(rating_a: float, rating_b: float) -> float:
    """Probability that A beats B given raw rating difference (no adjustments)."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def mov_multiplier(margin: int, elo_diff_winner: float) -> float:
    """FiveThirtyEight-style margin multiplier: bigger wins count more, but
    with diminishing returns, and blowouts by heavy favourites are discounted."""
    return math.log(abs(margin) + 1) * (2.2 / ((elo_diff_winner * 0.001) + 2.2))


def update_elo(home_elo: float, away_elo: float, home_score: int, away_score: int,
               neutral: bool = False, k: float = config.ELO_K) -> tuple[float, float]:
    """Return (new_home_elo, new_away_elo) after one completed game."""
    hfa = 0.0 if neutral else config.ELO_HOME_ADVANTAGE
    exp_home = expected_score(home_elo + hfa, away_elo)
    if home_score > away_score:
        actual_home = 1.0
    elif home_score < away_score:
        actual_home = 0.0
    else:
        actual_home = 0.5
    margin = home_score - away_score
    if margin == 0:
        mult = 1.0
    else:
        winner_diff = (home_elo + hfa - away_elo) if margin > 0 else (away_elo - home_elo - hfa)
        mult = mov_multiplier(margin, winner_diff)
    delta = k * mult * (actual_home - exp_home)
    return home_elo + delta, away_elo - delta


def regress_to_mean(elo: float, fraction: float = config.ELO_SEASON_REGRESSION) -> float:
    return elo + (config.ELO_START - elo) * fraction


class RatingEngine:
    """Replays the game history chronologically and exposes rating snapshots
    as of any (season, week)."""

    def __init__(self, games: list[Game]):
        self.games = sorted(games, key=lambda g: (g.gameday, g.gametime or "", g.game_id))
        self._snapshots: dict[tuple[int, int], RatingSnapshot] = {}
        self._final: RatingSnapshot | None = None
        self._build()

    def _build(self) -> None:
        elo: dict[str, float] = defaultdict(lambda: config.ELO_START)
        history: dict[str, TeamHistory] = defaultdict(TeamHistory)
        season_rec: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        current_season: int | None = None
        seen_keys: set[tuple[int, int]] = set()

        def snapshot(season: int, week: int) -> RatingSnapshot:
            return RatingSnapshot(
                season=season,
                week=week,
                elo=dict(elo),
                history={t: TeamHistory(deque(h.margins, maxlen=h.margins.maxlen),
                                        deque(h.points_for, maxlen=h.points_for.maxlen),
                                        deque(h.points_against, maxlen=h.points_against.maxlen),
                                        h.wins, h.losses, h.ties) for t, h in history.items()},
                season_records={t: tuple(r) for t, r in season_rec.items()},
            )

        for g in self.games:
            if g.season != current_season:
                if current_season is not None:
                    for t in list(elo):
                        elo[t] = regress_to_mean(elo[t])
                season_rec = defaultdict(lambda: [0, 0, 0])
                current_season = g.season
            key = (g.season, g.week)
            if key not in seen_keys:
                seen_keys.add(key)
                self._snapshots[key] = snapshot(g.season, g.week)
            if not g.completed:
                continue
            elo[g.home_team], elo[g.away_team] = update_elo(
                elo[g.home_team], elo[g.away_team], g.home_score, g.away_score, g.neutral_site
            )
            history[g.home_team].record(g.home_score, g.away_score)
            history[g.away_team].record(g.away_score, g.home_score)
            if g.game_type == "REG":
                for team, pf, pa in ((g.home_team, g.home_score, g.away_score),
                                     (g.away_team, g.away_score, g.home_score)):
                    idx = 0 if pf > pa else 1 if pa > pf else 2
                    season_rec[team][idx] += 1
        self._final = snapshot(current_season or 0, 99)

    def snapshot_for(self, season: int, week: int) -> RatingSnapshot:
        """Ratings before (season, week). Falls back to the closest earlier
        snapshot, or the final state if the week is beyond the data."""
        if (season, week) in self._snapshots:
            return self._snapshots[(season, week)]
        earlier = [k for k in self._snapshots if k < (season, week)]
        if earlier:
            return self._snapshots[max(earlier)]
        return self._final  # type: ignore[return-value]

    @property
    def latest(self) -> RatingSnapshot:
        return self._final  # type: ignore[return-value]

    def rankings(self, snap: RatingSnapshot) -> list[tuple[str, float]]:
        return sorted(snap.elo.items(), key=lambda kv: kv[1], reverse=True)
