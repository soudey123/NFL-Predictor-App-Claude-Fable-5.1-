"""Tests for the rating engine replay and the data parsers, using small
inline fixtures plus the bundled snapshot."""
from datetime import date

import pytest

from app import config
from app.data.sources import DataError, Game, parse_games_csv, parse_injuries, parse_scoreboard, qb_is_out, Injury
from app.model.ratings import RatingEngine, regress_to_mean

CSV_HEADER = ("game_id,season,game_type,week,gameday,gametime,away_team,away_score,home_team,home_score,"
              "location,away_rest,home_rest,away_qb_name,home_qb_name,stadium,roof,espn\n")


def mk(game_id, season, week, gameday, away, home, as_="", hs="", loc="Home", ar="7", hr="7"):
    return f"{game_id},{season},REG,{week},{gameday},13:00,{away},{as_},{home},{hs},{loc},{ar},{hr},A QB,H QB,Stadium,outdoors,1\n"


def test_parse_games_csv_handles_scores_missing_and_bad_rows():
    text = CSV_HEADER + mk("g1", 2025, 1, "2025-09-07", "KC", "BUF", 20, 27) \
        + mk("g2", 2026, 1, "2026-09-13", "LAR", "WSH") \
        + "broken,row,without,enough,columns\n" \
        + mk("g3", 2026, 1, "not-a-date", "KC", "BUF")
    games = parse_games_csv(text)
    assert [g.game_id for g in games] == ["g1", "g2"]
    g1, g2 = games
    assert g1.completed and g1.winner == "BUF"
    assert not g2.completed and g2.winner is None
    assert (g2.away_team, g2.home_team) == ("LA", "WAS")   # ESPN codes normalised
    assert g2.kickoff_utc.isoformat() == "2026-09-13T17:00:00+00:00"


def test_parse_games_csv_rejects_wrong_schema():
    with pytest.raises(DataError):
        parse_games_csv("foo,bar\n1,2\n")


def test_bundled_snapshot_loads_and_has_full_2026_schedule():
    games = parse_games_csv(config.SNAPSHOT_PATH.read_text("utf-8"))
    reg26 = [g for g in games if g.season == 2026 and g.game_type == "REG"]
    assert len(reg26) == 272
    assert {g.week for g in reg26} == set(range(1, 19))
    assert all(not g.completed for g in reg26)
    assert sum(1 for g in games if g.season == 2025 and g.completed) == 285


def test_rating_engine_replays_and_snapshots():
    text = CSV_HEADER \
        + mk("a", 2025, 1, "2025-09-07", "KC", "BUF", 10, 30) \
        + mk("b", 2025, 2, "2025-09-14", "BUF", "KC", 28, 7) \
        + mk("c", 2026, 1, "2026-09-13", "KC", "BUF")
    eng = RatingEngine(parse_games_csv(text))
    before = eng.snapshot_for(2025, 1)
    assert before.elo.get("BUF", config.ELO_START) == config.ELO_START
    wk2 = eng.snapshot_for(2025, 2)
    assert wk2.elo["BUF"] > config.ELO_START > wk2.elo["KC"]
    s26 = eng.snapshot_for(2026, 1)
    # regression toward the mean between seasons: 2026 opener rating is the
    # end-of-2025 rating pulled 1/3 of the way back to 1500
    end_2025 = RatingEngine(parse_games_csv(CSV_HEADER
        + mk("a", 2025, 1, "2025-09-07", "KC", "BUF", 10, 30)
        + mk("b", 2025, 2, "2025-09-14", "BUF", "KC", 28, 7))).latest.elo["BUF"]
    assert s26.elo["BUF"] == pytest.approx(regress_to_mean(end_2025))
    assert config.ELO_START < s26.elo["BUF"] < end_2025
    assert s26.season_records == {}            # new season, no games yet
    assert wk2.season_records["BUF"] == (1, 0, 0)
    assert s26.history["BUF"].wins == 2
    # unknown future week falls back to latest snapshot instead of crashing
    assert eng.snapshot_for(2026, 40).elo == eng.latest.elo


def test_parse_scoreboard_and_injuries_tolerate_malformed_payloads():
    sb = {"events": [
        {"id": "1", "competitions": [{"status": {"type": {"state": "post", "completed": True, "shortDetail": "Final"}},
                                      "competitors": [{"homeAway": "home", "team": {"abbreviation": "WSH"}, "score": "24"},
                                                      {"homeAway": "away", "team": {"abbreviation": "DAL"}, "score": "20"}]}]},
        {"id": "2"},   # missing competitions -> skipped
    ]}
    out = parse_scoreboard(sb)
    assert len(out) == 1 and out[0].home_abbr == "WAS" and out[0].home_score == 24 and out[0].completed
    inj = parse_injuries({"injuries": [
        {"displayName": "Kansas City Chiefs", "injuries": [
            {"status": "Out", "athlete": {"displayName": "Patrick Mahomes", "position": {"abbreviation": "QB"}}, "shortComment": "x"}]},
        {"displayName": "Not A Team", "injuries": [{"status": "Out"}]},
    ]})
    assert len(inj) == 1 and inj[0].team == "KC" and inj[0].position == "QB"


def test_qb_is_out_matches_names_loosely():
    injuries = [Injury("ATL", "Michael Penix Jr.", "QB", "Out", "Knee", ""),
                Injury("ATL", "Kirk Cousins", "QB", "Questionable", "", ""),
                Injury("ATL", "Bijan Robinson", "RB", "Out", "", "")]
    assert qb_is_out("Michael Penix", injuries) is not None
    assert qb_is_out("Kirk Cousins", injuries) is None          # questionable != out
    assert qb_is_out("Bijan Robinson", injuries) is None        # not a QB
    assert qb_is_out(None, injuries) is None
