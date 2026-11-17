"""
Player Matching Module — joins Transfermarkt ages and market values to
StatsBomb players.

The two sources write names differently ("Lionel Andrés Messi Cuccittini" in
StatsBomb vs "Lionel Messi" on Transfermarkt), so players are matched by:
  1. season  - only Transfermarkt players with a valuation during 2015/16
               (any league: the dataset's league code is the player's CURRENT
               league, so it cannot be used to filter 2015/16 players)
  2. name    - a similarity score: +0.5 if every word of the Transfermarkt
               name appears in the StatsBomb name or nickname, plus a
               0-1 text-similarity ratio
  3. nationality - +0.2 if both sources give the same nationality
               (breaks ties between players with the same name)
  4. safety  - a match is accepted only if it scores >= MIN_SCORE and is
               clearly better than the next-best candidate; each Transfermarkt
               player can be used once. Implausible ages are flagged for review.
Unmatched players are kept and flagged, never filled with invented values.
Manual fixes can be added to config/manual_matches.csv
(columns: sb_player_id, tm_player_id).

Input files (download from Kaggle "davidcariboo/player-scores"):
    data/raw/transfermarkt/players.csv
    data/raw/transfermarkt/player_valuations.csv
Usage:
    python -m scoutiq.cleaning.player_matching
Output:
    data/processed/player_profiles.parquet (+ .csv)
"""
from __future__ import annotations

import difflib
import re
import unicodedata

import pandas as pd

from scoutiq.config import PROJECT_ROOT, load_settings

LEAGUE_TO_TM = {"premier_league": "GB1", "la_liga": "ES1", "serie_a": "IT1",
                "ligue_1": "FR1", "bundesliga": "L1"}
SEASON_START, SEASON_END = pd.Timestamp("2015-07-01"), pd.Timestamp("2016-06-30")
REFERENCE_DATE = pd.Timestamp("2016-01-01")   # age and value are measured mid-season
MIN_SCORE = 0.85
MIN_MARGIN = 0.05
NATIONALITY_BONUS = 0.2
AGE_REVIEW_RANGE = (16, 41)   # ages outside this range are flagged for manual review
REQUIRED_COLUMNS = {
    "players.csv": {"player_id", "name", "date_of_birth"},
    "player_valuations.csv": {"player_id", "date", "market_value_in_eur",
                              "player_club_domestic_competition_id"},
}


# ---------- name helpers ----------

def normalise(name) -> str:
    """'Iñaki Williams-Arthuer' -> 'inaki williams arthuer'"""
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", text.lower())).strip()


def name_score(sb_names: list[str], tm_name: str) -> float:
    """Similarity between a StatsBomb player (full name + nickname) and a TM name."""
    tm_norm = normalise(tm_name)
    tm_tokens = set(tm_norm.split())
    best = 0.0
    for candidate in sb_names:
        cand = normalise(candidate)
        if not cand:
            continue
        score = difflib.SequenceMatcher(None, cand, tm_norm).ratio()
        if tm_tokens and tm_tokens <= set(cand.split()):
            score += 0.5
        best = max(best, score)
    return best


# ---------- loading ----------

def load_transfermarkt() -> tuple[pd.DataFrame, pd.DataFrame]:
    folder = PROJECT_ROOT / load_settings()["paths"]["raw"] / "transfermarkt"
    frames = []
    for file, needed in REQUIRED_COLUMNS.items():
        path = folder / file
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}. Download it from Kaggle (davidcariboo/player-scores).")
        df = pd.read_csv(path)
        missing = needed - set(df.columns)
        if missing:
            raise ValueError(f"{file} is missing columns {missing}. Columns found: {list(df.columns)}")
        frames.append(df)
    return frames[0], frames[1]


def season_valuations(valuations: pd.DataFrame) -> pd.DataFrame:
    """One valuation per player: the one closest to mid-season 2015/16."""
    v = valuations.copy()
    v["date"] = pd.to_datetime(v["date"])
    v = v[(v.date >= SEASON_START) & (v.date <= SEASON_END)]
    v["distance"] = (v.date - REFERENCE_DATE).abs()
    return v.sort_values("distance").groupby("player_id").head(1)


# ---------- matching ----------

def _tokens(text) -> set[str]:
    """Words of 3+ letters, used to find candidate names quickly."""
    return {w for w in normalise(text).split() if len(w) >= 3}


def match_players(sb: pd.DataFrame, tm_players: pd.DataFrame, tm_vals: pd.DataFrame,
                  overrides: dict[int, int] | None = None) -> pd.DataFrame:
    """Return one row per StatsBomb player with the matched Transfermarkt data (or none)."""
    cols = ["player_id", "name", "date_of_birth"]
    has_nat = "country_of_citizenship" in tm_players.columns
    if has_nat:
        cols.append("country_of_citizenship")
    pool = tm_vals.merge(tm_players[cols], on="player_id").set_index("player_id")
    overrides = overrides or {}

    # Word index: word -> Transfermarkt players whose name contains it
    index: dict[str, set[int]] = {}
    for tm_id, name in pool["name"].items():
        for w in _tokens(name):
            index.setdefault(w, set()).add(tm_id)

    candidates = []  # (score, sb_id, league, tm_id)
    for r in sb.itertuples():
        if r.player_id in overrides:
            continue
        sb_names = [r.player_name, r.player_nickname]
        possible = set()
        for n in sb_names:
            for w in _tokens(n):
                possible |= index.get(w, set())
        scored = []
        for tm_id in possible:
            score = name_score(sb_names, pool.at[tm_id, "name"])
            if has_nat and normalise(r.country) and normalise(r.country) == normalise(pool.at[tm_id, "country_of_citizenship"]):
                score += NATIONALITY_BONUS
            scored.append((score, tm_id))
        if not scored:
            continue
        scored.sort(reverse=True)
        best = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        if best[0] >= MIN_SCORE and best[0] - second >= MIN_MARGIN:
            candidates.append((best[0], r.player_id, r.league, best[1]))

    # Each Transfermarkt player can be used once: keep the strongest claim
    used_tm, chosen = set(overrides.values()), {}
    for score, sb_id, league, tm_id in sorted(candidates, reverse=True):
        if tm_id in used_tm or (sb_id, league) in chosen:
            continue
        used_tm.add(tm_id)
        chosen[(sb_id, league)] = (tm_id, round(score, 3), "auto")
    for sb_id, tm_id in overrides.items():
        for league in sb.loc[sb.player_id == sb_id, "league"]:
            chosen[(sb_id, league)] = (tm_id, None, "manual")

    all_tm = tm_players.set_index("player_id")
    rows = []
    for r in sb.itertuples():
        row = {"player_id": r.player_id, "league": r.league, "player_name": r.player_name}
        match = chosen.get((r.player_id, r.league))
        if match:
            tm_id, score, method = match
            dob = pd.to_datetime(all_tm.at[tm_id, "date_of_birth"], errors="coerce") if tm_id in all_tm.index else pd.NaT
            age = round((REFERENCE_DATE - dob).days / 365.25, 1) if pd.notna(dob) else None
            suspicious = age is not None and not (AGE_REVIEW_RANGE[0] <= age <= AGE_REVIEW_RANGE[1])
            row.update({
                "tm_player_id": tm_id,
                "tm_name": all_tm.at[tm_id, "name"] if tm_id in all_tm.index else None,
                "date_of_birth": dob,
                "age": age,
                "market_value_eur": pool.at[tm_id, "market_value_in_eur"] if tm_id in pool.index else None,
                "valuation_date": pool.at[tm_id, "date"] if tm_id in pool.index else None,
                "match_score": score, "match_method": method,
                "match_status": "review" if suspicious and method == "auto" else "matched",
            })
        else:
            row["match_status"] = "unmatched"
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    settings = load_settings()
    processed = PROJECT_ROOT / settings["paths"]["processed"]
    sb = pd.read_parquet(processed / "player_season_stats.parquet")
    tm_players, valuations = load_transfermarkt()
    tm_vals = season_valuations(valuations)
    print(f"Transfermarkt players with a 2015/16 valuation: {len(tm_vals)}")

    overrides_path = PROJECT_ROOT / "config" / "manual_matches.csv"
    overrides = {}
    if overrides_path.exists():
        o = pd.read_csv(overrides_path)
        overrides = dict(zip(o.sb_player_id, o.tm_player_id))
        print(f"Applying {len(overrides)} manual matches")

    profiles = match_players(sb, tm_players, tm_vals, overrides)
    profiles.to_parquet(processed / "player_profiles.parquet", index=False)
    profiles.to_csv(processed / "player_profiles.csv", index=False)

    merged = sb[["player_id", "league", "minutes"]].merge(profiles, on=["player_id", "league"])
    for label, part in [("All players", merged), ("Players with 900+ minutes", merged[merged.minutes >= 900])]:
        rate = (part.match_status == "matched").mean() * 100
        print(f"{label}: {len(part)} | matched {rate:.1f}%")

    print("\nSpot-check (random matched pairs — names should be the same person):")
    sample = merged[merged.match_status == "matched"].sample(10, random_state=2)
    print(sample[["player_name", "tm_name", "age", "market_value_eur", "match_score"]].to_string(index=False))

    review = merged[merged.match_status == "review"]
    print(f"\nFlagged for review (implausible age): {len(review)}")
    if len(review):
        print(review.nlargest(10, "minutes")[["player_id", "player_name", "tm_name", "age", "minutes"]].to_string(index=False))

    print("\nUnmatched players with the most minutes (candidates for config/manual_matches.csv):")
    print(merged[merged.match_status == "unmatched"].nlargest(10, "minutes")[
        ["player_id", "player_name", "league", "minutes"]].to_string(index=False))


if __name__ == "__main__":
    main()