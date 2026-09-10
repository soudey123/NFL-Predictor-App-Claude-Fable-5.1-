"""Unit tests for the prediction maths (no data files, no network)."""
from collections import deque

import pytest

from app import config
from app.model.predictor import Overrides, predict_game, rest_adjustment, form_adjustment, split_score
from app.model.ratings import RatingSnapshot, TeamHistory, expected_score, update_elo, regress_to_mean


def snap(home_elo=1500.0, away_elo=1500.0, home_hist=None, away_hist=None):
    return RatingSnapshot(season=2026, week=1, elo={"HOME": home_elo, "AWAY": away_elo},
                          history={k: v for k, v in (("HOME", home_hist), ("AWAY", away_hist)) if v},
                          season_records={})


def hist(*margins, base=20):
    """History with exactly these margins (points for = base + margin)."""
    h = TeamHistory()
    for m in margins:
        h.record(base + m, base)
    return h


# ---- Elo primitives --------------------------------------------------------
def test_expected_score_symmetric_and_bounded():
    assert expected_score(1500, 1500) == pytest.approx(0.5)
    assert expected_score(1600, 1500) + expected_score(1500, 1600) == pytest.approx(1.0)
    assert 0.0 < expected_score(1000, 2000) < 0.05


def test_update_elo_is_zero_sum_and_rewards_winner():
    h, a = update_elo(1500, 1500, 27, 17)
    assert h > 1500 > a
    assert h + a == pytest.approx(3000)


def test_bigger_margin_moves_rating_more_with_diminishing_returns():
    small, _ = update_elo(1500, 1500, 21, 20)
    big, _ = update_elo(1500, 1500, 42, 0)
    huge, _ = update_elo(1500, 1500, 70, 0)
    assert small < big < huge
    assert (huge - big) < (big - small)


def test_upset_moves_more_than_expected_win():
    fav_win, _ = update_elo(1700, 1400, 24, 20)      # strong home team wins narrowly
    _, dog_after = update_elo(1700, 1400, 20, 24)   # underdog wins on the road
    assert (dog_after - 1400) > (fav_win - 1700)


def test_season_regression_pulls_toward_mean():
    assert regress_to_mean(1650) == pytest.approx(1600)
    assert regress_to_mean(1350) == pytest.approx(1400)
    assert regress_to_mean(1500) == 1500


# ---- factor helpers --------------------------------------------------------
def test_rest_adjustment_clamped():
    assert rest_adjustment(7) == 0
    assert rest_adjustment(14) == config.REST_ADJ_MAX
    assert rest_adjustment(3) == pytest.approx(-16.0)
    assert rest_adjustment(1) == config.REST_ADJ_MIN
    assert rest_adjustment(10) == pytest.approx(12.0)


def test_form_adjustment_capped_and_signed():
    assert form_adjustment(None) == 0
    assert form_adjustment(hist(30, 30, 30, 30, 30)) == config.FORM_ADJ_MAX
    assert form_adjustment(hist(-30, -30, -30)) == -config.FORM_ADJ_MAX
    assert form_adjustment(hist(4, 4, 4, 4, 4)) == pytest.approx(10.0)


def test_split_score_never_ties_and_respects_margin():
    h, a = split_score(45.0, 3.0, "HOME", "AWAY")
    assert h > a and h + a in (45, 46)
    h, a = split_score(44.0, 0.0, "HOME", "AWAY")
    assert h != a
    h, a = split_score(44.0, -0.4, "HOME", "AWAY")
    assert a > h


# ---- predict_game ----------------------------------------------------------
def test_even_teams_home_field_is_only_edge():
    p = predict_game(snap(), "HOME", "AWAY")
    assert p.total_elo_diff == pytest.approx(config.ELO_HOME_ADVANTAGE)
    assert 0.5 < p.home_win_prob < 0.6
    assert p.predicted_winner == "HOME"
    assert p.home_score > p.away_score


def test_neutral_site_removes_home_field():
    p = predict_game(snap(), "HOME", "AWAY", neutral_site=True)
    assert p.total_elo_diff == pytest.approx(0.0)
    assert p.home_win_prob == pytest.approx(0.5)


def test_stronger_team_favoured_and_probabilities_sum_to_one():
    p = predict_game(snap(home_elo=1450, away_elo=1650), "HOME", "AWAY")
    d = p.to_dict()
    assert p.predicted_winner == "AWAY"
    assert d["home_win_prob"] + d["away_win_prob"] == pytest.approx(1.0, abs=1e-3)
    assert p.away_score > p.home_score


def test_qb_out_reduces_that_teams_chances():
    base = predict_game(snap(), "HOME", "AWAY")
    home_out = predict_game(snap(), "HOME", "AWAY", home_qb_out=True)
    away_out = predict_game(snap(), "HOME", "AWAY", away_qb_out=True)
    assert home_out.home_win_prob < base.home_win_prob < away_out.home_win_prob
    assert home_out.total_elo_diff == pytest.approx(base.total_elo_diff - config.QB_OUT_PENALTY)


def test_overrides_take_precedence_and_are_clamped():
    ov = Overrides.from_dict({"home_qb_out": True, "qb_penalty": 999, "home_rest": 50, "away_extra": -1000})
    assert ov.qb_penalty == 200
    assert ov.home_rest == 21
    assert ov.away_extra == -150
    p = predict_game(snap(), "HOME", "AWAY", home_qb_out=False, overrides=ov)
    qb = next(f for f in p.factors if f.key == "qb")
    assert qb.elo == -200
    assert any(f.key == "manual" for f in p.factors)


def test_overrides_ignore_garbage_values():
    ov = Overrides.from_dict({"qb_penalty": "abc", "home_rest": None, "neutral_site": 1})
    assert ov.qb_penalty is None and ov.home_rest is None and ov.neutral_site is True


def test_factors_sum_to_total_and_points_conversion():
    p = predict_game(snap(1580, 1520, hist(7, 7, 7), hist(-3, -3, -3)), "HOME", "AWAY", home_rest=14, away_rest=6)
    assert sum(f.elo for f in p.factors) == pytest.approx(p.total_elo_diff)
    assert p.margin == pytest.approx(p.total_elo_diff / config.ELO_POINTS_PER_POINT)
    assert {f.key for f in p.factors} == {"elo", "home", "rest", "form", "qb"}


def test_scores_reflect_team_scoring_profiles():
    high = TeamHistory(deque([10]), deque([35, 33, 31]), deque([28, 30, 27]))
    low = TeamHistory(deque([-3]), deque([14, 17, 13]), deque([17, 16, 20]))
    p_high = predict_game(snap(home_hist=high, away_hist=high), "HOME", "AWAY")
    p_low = predict_game(snap(home_hist=low, away_hist=low), "HOME", "AWAY")
    assert p_high.home_score + p_high.away_score > p_low.home_score + p_low.away_score
