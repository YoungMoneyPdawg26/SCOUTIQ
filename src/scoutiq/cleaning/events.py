"""
Data Cleaning & Preprocessing Module.

Loads the raw StatsBomb files downloaded by scoutiq.collection.statsbomb and
prepares them for feature engineering:
  * minutes played per player per match (from lineup position history)
  * the position played in each stint, mapped to ScoutIQ's 7 position groups
"""
from __future__ import annotations

from typing import Iterator

from scoutiq.collection.statsbomb import _raw_root, read_gz_json
from scoutiq.config import load_settings

# StatsBomb position names -> ScoutIQ position groups
POSITION_GROUPS = {
    "Goalkeeper": "GK",
    "Right Center Back": "CB", "Left Center Back": "CB", "Center Back": "CB",
    "Right Back": "FB", "Left Back": "FB", "Right Wing Back": "FB", "Left Wing Back": "FB",
    "Center Defensive Midfield": "DM", "Right Defensive Midfield": "DM",
    "Left Defensive Midfield": "DM",
    "Center Midfield": "CM", "Right Center Midfield": "CM", "Left Center Midfield": "CM",
    "Center Attacking Midfield": "CM",
    "Right Midfield": "W", "Left Midfield": "W", "Right Wing": "W", "Left Wing": "W",
    "Right Attacking Midfield": "W", "Left Attacking Midfield": "W",
    "Center Forward": "ST", "Right Center Forward": "ST", "Left Center Forward": "ST",
    "Secondary Striker": "ST",
}

GROUP_NAMES = {
    "GK": "Goalkeeper", "CB": "Centre-Back", "FB": "Full-Back", "DM": "Defensive Midfielder",
    "CM": "Central Midfielder", "W": "Winger", "ST": "Striker",
}


def parse_clock(clock: str) -> float:
    """Convert a StatsBomb match clock 'MM:SS' into minutes (e.g. '45:30' -> 45.5)."""
    minutes, seconds = clock.split(":")
    return int(minutes) + int(seconds) / 60


def match_end_minute(events: list[dict]) -> float:
    """Last moment of the match, taken from the final event's clock time."""
    return max(e["minute"] + e["second"] / 60 for e in events)


def player_stints(lineups: list[dict], end_minute: float) -> list[dict]:
    """
    One row per player per position stint in a match.

    StatsBomb's lineups list each position a player held with 'from' and 'to'
    clock times. A missing 'to' means the player stayed on until the end.
    Players with no positions (unused substitutes) produce no rows.
    """
    rows = []
    for team in lineups:
        for player in team["lineup"]:
            for stint in player.get("positions") or []:
                start = parse_clock(stint["from"])
                end = parse_clock(stint["to"]) if stint.get("to") else end_minute
                rows.append({
                    "team_name": team["team_name"],
                    "player_id": player["player_id"],
                    "player_name": player["player_name"],
                    "player_nickname": player.get("player_nickname"),
                    "country": (player.get("country") or {}).get("name"),
                    "position": stint["position"],
                    "position_group": POSITION_GROUPS.get(stint["position"]),
                    "minutes": max(0.0, end - start),
                })
    return rows


def iter_league_matches(league: str) -> Iterator[tuple[int, list[dict], list[dict]]]:
    """Yield (match_id, events, lineups) for every downloaded match of a league.
    Matches with missing files are skipped and reported."""
    settings = load_settings()
    raw_root = _raw_root()
    matches = read_gz_json(
        raw_root / "matches" / f"{settings['competitions'][league]}_{settings['season_id']}.json.gz"
    )
    missing = 0
    for match in matches:
        match_id = match["match_id"]
        events_path = raw_root / "events" / f"{match_id}.json.gz"
        lineups_path = raw_root / "lineups" / f"{match_id}.json.gz"
        if not (events_path.exists() and lineups_path.exists()):
            missing += 1
            continue
        yield match_id, read_gz_json(events_path), read_gz_json(lineups_path)
    if missing:
        print(f"  ! {league}: {missing} matches skipped (files missing)")