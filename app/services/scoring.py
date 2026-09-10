"""Leaderboard maths: grade stored predictions and user picks against results."""
from __future__ import annotations

from collections import defaultdict


def grade(predictions: dict[str, dict], picks: dict[str, dict], results: dict[str, dict],
          games_meta: dict[str, dict]) -> dict:
    """Return leaderboard rows and per-week breakdown.

    games_meta maps game_id -> {"home_team", "away_team", "week"} for every game in
    the season so the 'always home' baseline can be graded on the same set."""
    entities = {
        "model": {"name": "Model", "graded": 0, "correct": 0, "brier_sum": 0.0, "retroactive": 0,
                  "score_exact": 0, "score_abs_err": 0.0, "scored": 0},
        "you": {"name": "You", "graded": 0, "correct": 0, "brier_sum": 0.0, "retroactive": 0,
                "score_exact": 0, "score_abs_err": 0.0, "scored": 0},
        "home": {"name": "Always pick home", "graded": 0, "correct": 0, "brier_sum": 0.0, "retroactive": 0,
                 "score_exact": 0, "score_abs_err": 0.0, "scored": 0},
    }
    weekly: dict[int, dict] = defaultdict(lambda: {"week": 0, "games": 0, "model_correct": 0, "you_correct": 0,
                                                    "you_picks": 0, "home_correct": 0})

    for gid, res in results.items():
        meta = games_meta.get(gid)
        if not meta or res["winner"] == "TIE":
            continue
        week = meta["week"]
        wk = weekly[week]
        wk["week"] = week
        wk["games"] += 1

        # always-home baseline
        e = entities["home"]
        e["graded"] += 1
        hit = res["winner"] == meta["home_team"]
        e["correct"] += hit
        e["brier_sum"] += (1.0 - (1.0 if hit else 0.0)) ** 2
        wk["home_correct"] += hit

        pred = predictions.get(gid)
        if pred:
            e = entities["model"]
            e["graded"] += 1
            hit = pred["predicted_winner"] == res["winner"]
            e["correct"] += hit
            actual_home = 1.0 if res["winner"] == meta["home_team"] else 0.0
            e["brier_sum"] += (pred["home_win_prob"] - actual_home) ** 2
            e["retroactive"] += int(pred.get("retroactive") or 0)
            e["scored"] += 1
            e["score_abs_err"] += abs(pred["home_score"] - res["home_score"]) + abs(pred["away_score"] - res["away_score"])
            e["score_exact"] += int(pred["home_score"] == res["home_score"] and pred["away_score"] == res["away_score"])
            wk["model_correct"] += hit

        pick = picks.get(gid)
        if pick:
            e = entities["you"]
            e["graded"] += 1
            hit = pick["picked_team"] == res["winner"]
            e["correct"] += hit
            e["brier_sum"] += (1.0 - (1.0 if hit else 0.0)) ** 2
            wk["you_correct"] += hit
            wk["you_picks"] += 1
            if pick.get("home_score") is not None and pick.get("away_score") is not None:
                e["scored"] += 1
                e["score_abs_err"] += abs(pick["home_score"] - res["home_score"]) + abs(pick["away_score"] - res["away_score"])
                e["score_exact"] += int(pick["home_score"] == res["home_score"] and pick["away_score"] == res["away_score"])

    rows = []
    for key, e in entities.items():
        graded = e["graded"]
        rows.append({
            "key": key,
            "name": e["name"],
            "graded": graded,
            "correct": e["correct"],
            "accuracy": round(e["correct"] / graded, 4) if graded else None,
            "brier": round(e["brier_sum"] / graded, 4) if graded else None,
            "retroactive": e["retroactive"],
            "scored": e["scored"],
            "avg_score_error": round(e["score_abs_err"] / e["scored"], 1) if e["scored"] else None,
            "exact_scores": e["score_exact"],
        })
    rows.sort(key=lambda r: (-(r["accuracy"] or -1), r["brier"] if r["brier"] is not None else 9))
    return {"rows": rows, "weekly": [weekly[w] for w in sorted(weekly)]}


def agreement(predictions: dict[str, dict], picks: dict[str, dict]) -> dict:
    """How often the user's picks match the model's."""
    both = [g for g in picks if g in predictions]
    agree = sum(1 for g in both if picks[g]["picked_team"] == predictions[g]["predicted_winner"])
    return {"picks": len(picks), "compared": len(both), "agree": agree,
            "agree_rate": round(agree / len(both), 4) if both else None}
