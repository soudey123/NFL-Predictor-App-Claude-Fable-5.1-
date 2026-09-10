"""Leaderboard grading and prediction-locking behaviour."""
from app import db
from app.services.scoring import grade, agreement


META = {
    "g1": {"home_team": "SEA", "away_team": "NE", "week": 1},
    "g2": {"home_team": "LA", "away_team": "SF", "week": 1},
    "g3": {"home_team": "KC", "away_team": "DEN", "week": 2},
}
RESULTS = {
    "g1": {"winner": "SEA", "home_score": 27, "away_score": 20},
    "g2": {"winner": "SF", "home_score": 17, "away_score": 24},
    "g3": {"winner": "KC", "home_score": 21, "away_score": 21 - 1},
}
PREDS = {
    "g1": {"predicted_winner": "SEA", "home_win_prob": 0.7, "home_score": 25, "away_score": 20, "retroactive": 0},
    "g2": {"predicted_winner": "LA", "home_win_prob": 0.6, "home_score": 24, "away_score": 21, "retroactive": 1},
}
PICKS = {
    "g1": {"picked_team": "NE", "home_score": 20, "away_score": 27},
    "g2": {"picked_team": "SF", "home_score": None, "away_score": None},
}


def test_grade_accuracy_brier_and_baseline():
    out = grade(PREDS, PICKS, RESULTS, META)
    rows = {r["key"]: r for r in out["rows"]}
    model, you, home = rows["model"], rows["you"], rows["home"]
    assert model["graded"] == 2 and model["correct"] == 1 and model["accuracy"] == 0.5
    # Brier: g1 (0.7-1)^2=0.09, g2 (0.6-0)^2=0.36 -> mean 0.225
    assert model["brier"] == 0.225
    assert model["retroactive"] == 1
    assert model["avg_score_error"] == ((2 + 0) + (7 + 3)) / 2
    assert you["graded"] == 2 and you["correct"] == 1
    assert you["scored"] == 1 and you["avg_score_error"] == 14.0   # |20-27| + |27-20|
    assert home["graded"] == 3 and home["correct"] == 2      # SEA and KC won at home
    assert [w["week"] for w in out["weekly"]] == [1, 2]
    assert out["weekly"][0]["model_correct"] == 1 and out["weekly"][0]["you_picks"] == 2


def test_grade_ignores_ties_and_unknown_games():
    results = {"g1": {"winner": "TIE", "home_score": 20, "away_score": 20}, "zzz": RESULTS["g1"]}
    out = grade(PREDS, PICKS, results, META)
    assert all(r["graded"] == 0 for r in out["rows"])


def test_agreement_rate():
    ag = agreement(PREDS, PICKS)
    assert ag == {"picks": 2, "compared": 2, "agree": 0, "agree_rate": 0.0}   # both picks fade the model


def test_locked_prediction_is_never_overwritten(tmp_path):
    from app import config
    config.DB_PATH = tmp_path / "t.db"
    db.init_db()
    pred = {"home_win_prob": 0.6, "predicted_winner": "SEA", "home_score": 24, "away_score": 20, "inputs": {}}
    with db.connect() as conn:
        assert db.upsert_prediction(conn, "g1", 2026, 1, "SEA", "NE", pred, lock=False, retroactive=False)
        pred2 = {**pred, "home_win_prob": 0.9}
        assert db.upsert_prediction(conn, "g1", 2026, 1, "SEA", "NE", pred2, lock=True, retroactive=False)
        assert db.get_predictions(conn, 2026)["g1"]["home_win_prob"] == 0.9
        pred3 = {**pred, "home_win_prob": 0.1, "predicted_winner": "NE"}
        assert not db.upsert_prediction(conn, "g1", 2026, 1, "SEA", "NE", pred3, lock=True, retroactive=False)
        row = db.get_predictions(conn, 2026, 1)["g1"]
        assert row["home_win_prob"] == 0.9 and row["locked"] == 1
        # results upsert derives the winner and can be corrected later
        db.upsert_result(conn, "g1", 2026, 1, "SEA", "NE", 20, 27, "espn")
        assert db.get_results(conn, 2026)["g1"]["winner"] == "NE"
        db.upsert_result(conn, "g1", 2026, 1, "SEA", "NE", 30, 27, "nflverse")
        assert db.get_results(conn, 2026)["g1"]["winner"] == "SEA"
