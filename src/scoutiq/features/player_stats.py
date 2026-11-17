"""
Feature Engineering Module — player-season statistics.

Turns raw StatsBomb events into ONE ROW PER PLAYER PER LEAGUE with:
  * totals for every ScoutIQ metric
  * per-90-minute values (count metrics) and percentages (ratio metrics)
  * minutes, matches, team and primary position group

Every metric is labelled 'actual' (counted directly from StatsBomb events)
or 'derived' (calculated by ScoutIQ using a documented rule), as the project
brief requires.

Usage:
    python -m scoutiq.features.player_stats
Output:
    data/processed/player_season_stats.parquet  (+ .csv copy to open in Excel)
"""
from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd

from scoutiq.cleaning.events import (
    GROUP_NAMES, iter_league_matches, match_end_minute, player_stints,
)
from scoutiq.config import PROJECT_ROOT, load_settings

# ---------------------------------------------------------------------------
# Metric dictionary: one place that describes every metric.
#   category         -> used to group metrics in the scoring model
#   higher_is_better -> False for metrics where less is better (e.g. turnovers)
#   kind             -> 'count' (converted to per 90) or 'ratio' (a percentage)
#   status           -> 'actual' or 'derived'
# ---------------------------------------------------------------------------
METRICS = {
    # finishing
    "goals":              dict(label="Goals (non-penalty)", category="finishing", higher_is_better=True, kind="count", status="actual"),
    "npxg":               dict(label="Non-penalty xG", category="finishing", higher_is_better=True, kind="count", status="actual"),
    "shots":              dict(label="Shots (non-penalty)", category="finishing", higher_is_better=True, kind="count", status="actual"),
    # creation
    "assists":            dict(label="Assists", category="creation", higher_is_better=True, kind="count", status="actual"),
    "xa":                 dict(label="Expected assists (xA)", category="creation", higher_is_better=True, kind="count", status="derived"),
    "key_passes":         dict(label="Key passes", category="creation", higher_is_better=True, kind="count", status="actual"),
    # progression
    "progressive_passes": dict(label="Progressive passes", category="progression", higher_is_better=True, kind="count", status="derived"),
    "dribbles_completed": dict(label="Completed dribbles", category="progression", higher_is_better=True, kind="count", status="actual"),
    # defending
    "tackles":            dict(label="Tackles", category="defending", higher_is_better=True, kind="count", status="actual"),
    "interceptions":      dict(label="Interceptions", category="defending", higher_is_better=True, kind="count", status="actual"),
    "clearances":         dict(label="Clearances", category="defending", higher_is_better=True, kind="count", status="actual"),
    "blocks":             dict(label="Blocks", category="defending", higher_is_better=True, kind="count", status="actual"),
    "recoveries":         dict(label="Ball recoveries", category="defending", higher_is_better=True, kind="count", status="actual"),
    "pressures":          dict(label="Pressures", category="defending", higher_is_better=True, kind="count", status="actual"),
    "ground_duels_won":   dict(label="Ground duels won", category="defending", higher_is_better=True, kind="count", status="derived"),
    # aerial
    "aerials_won":        dict(label="Aerial duels won", category="aerial", higher_is_better=True, kind="count", status="derived"),
    "aerial_win_pct":     dict(label="Aerial duel win %", category="aerial", higher_is_better=True, kind="ratio", status="derived"),
    # retention
    "pass_completion_pct": dict(label="Pass completion %", category="retention", higher_is_better=True, kind="ratio", status="actual"),
    "turnovers":          dict(label="Turnovers (dispossessed + miscontrols)", category="retention", higher_is_better=False, kind="count", status="derived"),
    # goalkeeping
    "saves":              dict(label="Saves", category="shot_stopping", higher_is_better=True, kind="count", status="actual"),
    "save_pct":           dict(label="Save %", category="shot_stopping", higher_is_better=True, kind="ratio", status="derived"),
    "goals_prevented":    dict(label="Goals prevented (on-target xG − goals conceded)", category="shot_stopping", higher_is_better=True, kind="count", status="derived"),
    "goals_conceded":     dict(label="Goals conceded", category="shot_stopping", higher_is_better=False, kind="count", status="actual"),
    "claims":             dict(label="Claims / collections", category="command", higher_is_better=True, kind="count", status="actual"),
    "sweeper_actions":    dict(label="Sweeper actions", category="command", higher_is_better=True, kind="count", status="actual"),
    "gk_long_pass_pct":   dict(label="Long pass completion % (35+ yards)", category="distribution", higher_is_better=True, kind="ratio", status="derived"),
}

# Raw counters used to build the ratio metrics above
HELPER_COUNTS = [
    "passes", "passes_completed", "aerials_lost", "shots_on_target_faced",
    "on_target_xg_faced", "long_passes", "long_passes_completed",
]

SET_PIECE_PASSES = {"Corner", "Free Kick", "Throw-in", "Goal Kick", "Kick Off"}
SAVE_TYPES = {"Shot Saved", "Penalty Saved", "Shot Saved To Post", "Saved To Post", "Save"}
GOAL_CONCEDED_TYPES = {"Goal Conceded", "Penalty Conceded"}
TACKLE_WON = {"Won", "Success", "Success In Play", "Success Out"}
GOAL_X, GOAL_Y = 120.0, 40.0  # StatsBomb pitch is 120 x 80; teams always attack towards x = 120


def _dist_to_goal(x: float, y: float) -> float:
    return math.hypot(GOAL_X - x, GOAL_Y - y)


def _in_box(x: float, y: float) -> bool:
    return x >= 102 and 18 <= y <= 62


def is_progressive(event: dict) -> bool:
    """
    ScoutIQ's progressive pass rule (derived metric):
      a completed open-play pass, starting outside the team's own defensive 40%
      of the pitch, that either moves the ball at least 25% closer to the
      opponent's goal or enters the penalty area from outside it.
    """
    p = event["pass"]
    if "outcome" in p or (p.get("type") or {}).get("name") in SET_PIECE_PASSES:
        return False
    (x0, y0), (x1, y1) = event["location"][:2], p["end_location"][:2]
    if x0 < 48:
        return False
    if _dist_to_goal(x1, y1) <= 0.75 * _dist_to_goal(x0, y0):
        return True
    return _in_box(x1, y1) and not _in_box(x0, y0)


def count_match_events(events: list[dict], totals: dict) -> None:
    """Add one match's events to the running per-player totals."""
    shot_xg = {e["id"]: e["shot"].get("statsbomb_xg", 0.0) for e in events if "shot" in e}
    passer_of = {e["id"]: e["player"]["id"] for e in events if "pass" in e and "player" in e}

    for e in events:
        if "player" not in e:
            continue
        t = totals[e["player"]["id"]]
        etype = e["type"]["name"]

        if etype == "Shot":
            s = e["shot"]
            if (s.get("type") or {}).get("name") != "Penalty":
                t["shots"] += 1
                t["npxg"] += s.get("statsbomb_xg", 0.0)
                if s["outcome"]["name"] == "Goal":
                    t["goals"] += 1
            # xA: credit the shot's xG to the player who made the key pass
            if s.get("key_pass_id") in passer_of:
                totals[passer_of[s["key_pass_id"]]]["xa"] += s.get("statsbomb_xg", 0.0)
            if s.get("aerial_won"):
                t["aerials_won"] += 1

        elif etype == "Pass":
            p = e["pass"]
            t["passes"] += 1
            completed = "outcome" not in p
            t["passes_completed"] += completed
            if p.get("goal_assist"):
                t["assists"] += 1
                t["key_passes"] += 1
            elif p.get("shot_assist"):
                t["key_passes"] += 1
            if is_progressive(e):
                t["progressive_passes"] += 1
            if p.get("length", 0) >= 35:
                t["long_passes"] += 1
                t["long_passes_completed"] += completed
            if p.get("aerial_won"):
                t["aerials_won"] += 1

        elif etype == "Duel":
            d = e["duel"]
            dtype = (d.get("type") or {}).get("name")
            if dtype == "Tackle":
                t["tackles"] += 1
                if (d.get("outcome") or {}).get("name") in TACKLE_WON:
                    t["ground_duels_won"] += 1
            elif dtype == "Aerial Lost":
                t["aerials_lost"] += 1

        elif etype == "Dribble":
            if (e.get("dribble", {}).get("outcome") or {}).get("name") == "Complete":
                t["dribbles_completed"] += 1
                t["ground_duels_won"] += 1

        elif etype == "Interception":
            t["interceptions"] += 1
        elif etype == "Clearance":
            t["clearances"] += 1
            if e.get("clearance", {}).get("aerial_won"):
                t["aerials_won"] += 1
        elif etype == "Block":
            t["blocks"] += 1
        elif etype == "Ball Recovery":
            if not e.get("ball_recovery", {}).get("recovery_failure"):
                t["recoveries"] += 1
        elif etype == "Pressure":
            t["pressures"] += 1
        elif etype == "Dispossessed":
            t["turnovers"] += 1
        elif etype == "Miscontrol":
            t["turnovers"] += 1
            if e.get("miscontrol", {}).get("aerial_won"):
                t["aerials_won"] += 1

        elif etype == "Goal Keeper":
            gk_type = e["goalkeeper"]["type"]["name"]
            if gk_type in SAVE_TYPES or gk_type in GOAL_CONCEDED_TYPES:
                t["shots_on_target_faced"] += 1
                related_shot = next((r for r in e.get("related_events", []) if r in shot_xg), None)
                t["on_target_xg_faced"] += shot_xg.get(related_shot, 0.0)
                if gk_type in SAVE_TYPES:
                    t["saves"] += 1
                else:
                    t["goals_conceded"] += 1
            elif gk_type == "Collected":
                t["claims"] += 1
            elif gk_type in ("Keeper Sweeper", "Smother"):
                t["sweeper_actions"] += 1


def _ratio(num: float, den: float) -> float:
    return round(100 * num / den, 1) if den else float("nan")


def build_player_stats(leagues: list[str]) -> pd.DataFrame:
    """Build the player-season table for the given leagues."""
    rows = []
    for league in leagues:
        print(f"Processing {league} ...")
        totals: dict = defaultdict(lambda: defaultdict(float))
        stints = []
        n_matches = 0
        for match_id, events, lineups in iter_league_matches(league):
            n_matches += 1
            for s in player_stints(lineups, match_end_minute(events)):
                stints.append({**s, "match_id": match_id})
            count_match_events(events, totals)
        print(f"  {n_matches} matches read")

        st = pd.DataFrame(stints)
        # Team and primary position = where the player spent the most minutes
        by_team = st.groupby(["player_id", "team_name"])["minutes"].sum().reset_index()
        main_team = by_team.sort_values("minutes").groupby("player_id").tail(1).set_index("player_id")["team_name"]
        by_pos = st.dropna(subset=["position_group"]).groupby(["player_id", "position_group"])["minutes"].sum().reset_index()
        main_pos = by_pos.sort_values("minutes").groupby("player_id").tail(1).set_index("player_id")["position_group"]
        info = st.groupby("player_id").agg(
            player_name=("player_name", "first"), player_nickname=("player_nickname", "first"),
            country=("country", "first"), minutes=("minutes", "sum"), matches=("match_id", "nunique"),
        )

        for pid, r in info.iterrows():
            t = totals.get(pid, {})
            row = {
                "player_id": pid, "player_name": r.player_name, "player_nickname": r.player_nickname,
                "country": r.country, "league": league, "team": main_team.get(pid),
                "position_group": main_pos.get(pid), "matches": int(r.matches),
                "minutes": round(r.minutes, 1),
            }
            for m, meta in METRICS.items():
                if meta["kind"] == "count":
                    row[m] = round(t.get(m, 0.0), 3)
            for h in HELPER_COUNTS:
                row[h] = round(t.get(h, 0.0), 3)
            rows.append(row)

    df = pd.DataFrame(rows)
    df["position_name"] = df["position_group"].map(GROUP_NAMES)

    # Derived ratios and goals prevented
    df["pass_completion_pct"] = [_ratio(a, b) for a, b in zip(df.passes_completed, df.passes)]
    df["aerial_win_pct"] = [_ratio(a, a + b) for a, b in zip(df.aerials_won, df.aerials_lost)]
    df["save_pct"] = [_ratio(a, b) for a, b in zip(df.saves, df.shots_on_target_faced)]
    df["gk_long_pass_pct"] = [_ratio(a, b) for a, b in zip(df.long_passes_completed, df.long_passes)]
    df["goals_prevented"] = (df.on_target_xg_faced - df.goals_conceded).round(3)

    # Per-90 values for every count metric
    for m, meta in METRICS.items():
        if meta["kind"] == "count":
            df[f"{m}_p90"] = (df[m] / df["minutes"] * 90).where(df["minutes"] > 0).round(3)
    return df


def main() -> None:
    settings = load_settings()
    df = build_player_stats(settings["scoring_leagues"])
    out_dir = PROJECT_ROOT / settings["paths"]["processed"]
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / "player_season_stats.parquet", index=False)
    df.to_csv(out_dir / "player_season_stats.csv", index=False)

    eligible = df[df.minutes >= 900]
    print(f"\nSaved {len(df)} player-season rows to {out_dir}")
    print(f"Players with 900+ minutes: {len(eligible)}")
    print(eligible.groupby("position_name").size().to_string())
    print("\nSanity check: top 5 non-penalty xG per 90 (900+ min)")
    print(eligible.nlargest(5, "npxg_p90")[["player_name", "team", "minutes", "goals", "npxg_p90"]].to_string(index=False))


if __name__ == "__main__":
    main()