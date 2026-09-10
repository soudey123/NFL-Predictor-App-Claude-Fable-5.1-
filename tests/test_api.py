"""End-to-end API tests against the bundled snapshot (offline)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app import main
    with TestClient(main.app) as c:
        yield c


def test_meta_and_week(client):
    m = client.get("/api/meta").json()
    assert m["season"] == 2026 and 1 <= m["current_week"] <= 18
    wk = client.get(f"/api/weeks/{m['current_week']}").json()
    assert wk["games"]
    g = wk["games"][0]
    assert 0 <= g["prediction"]["home_win_prob"] <= 1
    assert g["prediction"]["predicted_winner"] in (g["home"]["abbr"], g["away"]["abbr"])
    assert client.get("/api/weeks/99").status_code == 404


def test_what_if_changes_prediction(client):
    wk = client.get("/api/weeks/1").json()
    gid = wk["games"][0]["game"]["game_id"]
    base = client.get(f"/api/games/{gid}").json()["prediction"]
    alt = client.post(f"/api/games/{gid}/what-if", json={"overrides": {"home_qb_out": True, "qb_penalty": 150}}).json()
    assert alt["home_win_prob"] < base["home_win_prob"]
    assert client.post("/api/games/nope/what-if", json={"overrides": {}}).status_code == 404


def test_pick_lifecycle_and_persistence(client):
    wk = client.get("/api/weeks/18").json()          # far in the future: never locked in tests
    g = wk["games"][0]
    gid, home = g["game"]["game_id"], g["home"]["abbr"]
    r = client.put(f"/api/picks/{gid}", json={"team": home, "home_score": 27, "away_score": 20})
    assert r.status_code == 200 and r.json()["picked_team"] == home
    assert client.put(f"/api/picks/{gid}", json={"team": "ZZZ"}).status_code == 422
    picks = client.get("/api/picks").json()
    assert any(p["game"]["game_id"] == gid for p in picks["picks"])
    assert client.get("/api/weeks/18").json()["games"][0]["pick"]["picked_team"] == home
    assert client.delete(f"/api/picks/{gid}").json()["deleted"] is True
    assert client.delete(f"/api/picks/{gid}").json()["deleted"] is False


def test_team_pages_and_leaderboard_shape(client):
    teams = client.get("/api/teams").json()
    assert len(teams) == 32 and teams[0]["rank"] == 1
    kc = client.get("/api/teams/KC").json()
    assert len(kc["schedule"]) == 17 and kc["recent"]
    assert client.get("/api/teams/LAR").status_code == 200      # ESPN alias accepted
    assert client.get("/api/teams/XYZ").status_code == 404
    lb = client.get("/api/leaderboard").json()
    assert {r["key"] for r in lb["rows"]} == {"model", "you", "home"}
