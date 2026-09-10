"""Static reference data for the 32 NFL teams.

Abbreviations follow nflverse (the primary data source). ESPN uses a few
different codes (LAR, WSH), captured in `espn_abbr` so the two feeds can be
joined. Colors and ESPN ids were taken from ESPN's public teams endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Team:
    abbr: str          # nflverse abbreviation (canonical in this app)
    espn_abbr: str     # ESPN abbreviation
    espn_id: str
    city: str
    nickname: str
    conference: str
    division: str
    color: str         # primary hex without '#'
    alt_color: str

    @property
    def name(self) -> str:
        return f"{self.city} {self.nickname}"

    @property
    def logo_url(self) -> str:
        return f"https://a.espncdn.com/i/teamlogos/nfl/500/scoreboard/{self.espn_abbr.lower()}.png"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["name"] = self.name
        d["logo_url"] = self.logo_url
        return d


_RAW = [
    # abbr, espn_abbr, espn_id, city, nickname, conf, div, color, alt
    ("ARI", "ARI", "22", "Arizona", "Cardinals", "NFC", "West", "a40227", "ffffff"),
    ("ATL", "ATL", "1", "Atlanta", "Falcons", "NFC", "South", "a71930", "000000"),
    ("BAL", "BAL", "33", "Baltimore", "Ravens", "AFC", "North", "29126f", "000000"),
    ("BUF", "BUF", "2", "Buffalo", "Bills", "AFC", "East", "00338d", "d50a0a"),
    ("CAR", "CAR", "29", "Carolina", "Panthers", "NFC", "South", "0085ca", "000000"),
    ("CHI", "CHI", "3", "Chicago", "Bears", "NFC", "North", "0b1c3a", "e64100"),
    ("CIN", "CIN", "4", "Cincinnati", "Bengals", "AFC", "North", "fb4f14", "000000"),
    ("CLE", "CLE", "5", "Cleveland", "Browns", "AFC", "North", "472a08", "ff3c00"),
    ("DAL", "DAL", "6", "Dallas", "Cowboys", "NFC", "East", "002a5c", "b0b7bc"),
    ("DEN", "DEN", "7", "Denver", "Broncos", "AFC", "West", "0a2343", "fc4c02"),
    ("DET", "DET", "8", "Detroit", "Lions", "NFC", "North", "0076b6", "bbbbbb"),
    ("GB", "GB", "9", "Green Bay", "Packers", "NFC", "North", "204e32", "ffb612"),
    ("HOU", "HOU", "34", "Houston", "Texans", "AFC", "South", "021018", "eb0028"),
    ("IND", "IND", "11", "Indianapolis", "Colts", "AFC", "South", "003b75", "ffffff"),
    ("JAX", "JAX", "30", "Jacksonville", "Jaguars", "AFC", "South", "007487", "d7a22a"),
    ("KC", "KC", "12", "Kansas City", "Chiefs", "AFC", "West", "e31837", "ffb612"),
    ("LA", "LAR", "14", "Los Angeles", "Rams", "NFC", "West", "003594", "ffd100"),
    ("LAC", "LAC", "24", "Los Angeles", "Chargers", "AFC", "West", "0080c6", "ffc20e"),
    ("LV", "LV", "13", "Las Vegas", "Raiders", "AFC", "West", "000000", "a5acaf"),
    ("MIA", "MIA", "15", "Miami", "Dolphins", "AFC", "East", "008e97", "fc4c02"),
    ("MIN", "MIN", "16", "Minnesota", "Vikings", "NFC", "North", "4f2683", "ffc62f"),
    ("NE", "NE", "17", "New England", "Patriots", "AFC", "East", "002a5c", "c60c30"),
    ("NO", "NO", "18", "New Orleans", "Saints", "NFC", "South", "d3bc8d", "000000"),
    ("NYG", "NYG", "19", "New York", "Giants", "NFC", "East", "003c7f", "c9243f"),
    ("NYJ", "NYJ", "20", "New York", "Jets", "AFC", "East", "115740", "ffffff"),
    ("PHI", "PHI", "21", "Philadelphia", "Eagles", "NFC", "East", "06424d", "000000"),
    ("PIT", "PIT", "23", "Pittsburgh", "Steelers", "AFC", "North", "000000", "ffb612"),
    ("SEA", "SEA", "26", "Seattle", "Seahawks", "NFC", "West", "002a5c", "69be28"),
    ("SF", "SF", "25", "San Francisco", "49ers", "NFC", "West", "aa0000", "b3995d"),
    ("TB", "TB", "27", "Tampa Bay", "Buccaneers", "NFC", "South", "bd1c36", "3e3a35"),
    ("TEN", "TEN", "10", "Tennessee", "Titans", "AFC", "South", "4495d2", "001532"),
    ("WAS", "WSH", "28", "Washington", "Commanders", "NFC", "East", "5a1414", "ffb612"),
]

TEAMS: dict[str, Team] = {r[0]: Team(*r) for r in _RAW}
ESPN_ABBR_TO_ABBR: dict[str, str] = {t.espn_abbr: t.abbr for t in TEAMS.values()}
NAME_TO_ABBR: dict[str, str] = {t.name: t.abbr for t in TEAMS.values()}

# Historical / alternate codes that can show up in older nflverse rows.
_ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA", "WSH": "WAS", "JAC": "JAX"}


def normalize_abbr(code: str | None) -> str | None:
    """Map any known team code (nflverse, ESPN, legacy) to the canonical abbr."""
    if not code:
        return None
    code = code.strip().upper()
    if code in TEAMS:
        return code
    return _ALIASES.get(code) or ESPN_ABBR_TO_ABBR.get(code)


def get_team(abbr: str) -> Team | None:
    key = normalize_abbr(abbr)
    return TEAMS.get(key) if key else None
