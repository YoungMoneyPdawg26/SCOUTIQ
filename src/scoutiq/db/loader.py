"""
Database Management Module — loads ScoutIQ's processed data into MySQL.

Steps: (1) rebuild the schema from schema.sql, (2) insert leagues, clubs,
players, player-seasons, metrics, stats, market values and scores,
(3) save the example club brief and its shortlist, (4) print row counts.

Usage:
    python -m scoutiq.db.loader
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import text

from scoutiq.config import PROJECT_ROOT, load_settings
from scoutiq.db.connection import get_engine
from scoutiq.features.player_stats import METRICS

LEAGUE_INFO = {
    "premier_league": ("Premier League", "England"), "la_liga": ("La Liga", "Spain"),
    "serie_a": ("Serie A", "Italy"), "ligue_1": ("Ligue 1", "France"),
    "bundesliga": ("Bundesliga", "Germany"),
}
SEASON = "2015/2016"


def run_schema(engine) -> None:
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    # Split into statements; drop comment-only lines first
    lines = [l for l in sql.splitlines() if not l.strip().startswith("--")]
    statements = [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _append(df: pd.DataFrame, table: str, engine) -> None:
    """Insert a DataFrame into an existing table. Missing values (NaN/NaT/NA) become SQL NULL."""
    clean = df.astype(object).where(df.notna(), None)
    clean.to_sql(table, engine, if_exists="append", index=False, chunksize=2000, method="multi")


def load_all() -> None:
    settings = load_settings()
    processed = PROJECT_ROOT / settings["paths"]["processed"]
    stats = pd.read_parquet(processed / "player_season_stats.parquet")
    scores = pd.read_parquet(processed / "performance_scores.parquet")
    profiles = pd.read_parquet(processed / "player_profiles.parquet")
    engine = get_engine()

    print("Rebuilding schema ...")
    run_schema(engine)

    leagues = pd.DataFrame([
        {"league_id": k, "name": LEAGUE_INFO[k][0], "country": LEAGUE_INFO[k][1],
         "statsbomb_competition_id": v, "in_scoring_pool": k in settings["scoring_leagues"]}
        for k, v in settings["competitions"].items()
    ])
    _append(leagues, "leagues", engine)

    clubs = (stats[["team", "league"]].drop_duplicates("team")
             .rename(columns={"team": "name", "league": "league_id"}))
    _append(clubs, "clubs", engine)
    club_ids = pd.read_sql("SELECT club_id, name FROM clubs", engine).set_index("name")["club_id"]

    prof = profiles.sort_values("match_status").drop_duplicates("player_id")  # 'matched' sorts first
    players = (stats.drop_duplicates("player_id")[["player_id", "player_name", "player_nickname", "country"]]
               .merge(prof[["player_id", "date_of_birth", "tm_player_id", "match_status"]], on="player_id", how="left"))
    players = players.rename(columns={"player_name": "name", "player_nickname": "nickname", "country": "nationality"})
    players["date_of_birth"] = pd.to_datetime(players["date_of_birth"]).dt.date
    players["tm_player_id"] = players["tm_player_id"].astype("Int64")
    players["match_status"] = players["match_status"].fillna("unmatched")
    _append(players, "players", engine)

    seasons = stats.merge(profiles[["player_id", "league", "age"]], on=["player_id", "league"], how="left")
    ps = pd.DataFrame({
        "player_id": seasons.player_id, "league_id": seasons.league, "club_id": seasons.team.map(club_ids),
        "season": SEASON, "position_group": seasons.position_group, "minutes": seasons.minutes,
        "matches": seasons.matches, "age": seasons.age,
    })
    _append(ps, "player_seasons", engine)
    ps_ids = pd.read_sql("SELECT player_season_id, player_id, league_id FROM player_seasons", engine)
    key = ps_ids.set_index(["player_id", "league_id"])["player_season_id"]

    metrics = pd.DataFrame([
        {"metric_code": m, "label": meta["label"], "category": meta["category"],
         "higher_is_better": meta["higher_is_better"], "kind": meta["kind"], "data_status": meta["status"]}
        for m, meta in METRICS.items()
    ])
    _append(metrics, "metrics", engine)

    rows = []
    for r in stats.itertuples(index=False):
        psid = key[(r.player_id, r.league)]
        rd = r._asdict()
        for m, meta in METRICS.items():
            if meta["kind"] == "count":
                rows.append((psid, m, rd[m], rd[f"{m}_p90"], None))
            else:
                rows.append((psid, m, None, None, rd[m]))
    player_stats = pd.DataFrame(rows, columns=["player_season_id", "metric_code", "total", "per90", "ratio_value"])
    _append(player_stats, "player_stats", engine)

    mv = profiles[profiles.market_value_eur.notna()].drop_duplicates("player_id")
    market_values = pd.DataFrame({
        "player_id": mv.player_id, "valuation_date": pd.to_datetime(mv.valuation_date).dt.date,
        "value_eur": mv.market_value_eur.astype("int64"),
    })
    _append(market_values, "market_values", engine)

    scores["player_season_id"] = [key[(p, l)] for p, l in zip(scores.player_id, scores.league)]
    _append(scores[["player_season_id", "model_version", "performance_score",
                    "rank_in_position", "players_in_position"]], "performance_scores", engine)
    comp = []
    for col in [c for c in scores.columns if c.startswith(("cat_", "pct_"))]:
        ctype, code = ("category", col[4:]) if col.startswith("cat_") else ("metric", col[4:])
        part = scores[["player_season_id", "model_version", col]].dropna().rename(columns={col: "score"})
        part["component_type"], part["component_code"] = ctype, code
        comp.append(part)
    _append(pd.concat(comp), "score_components", engine)

    # Save the example brief and its shortlist
    from scoutiq.db.queries import save_shortlist
    from scoutiq.ranking.shortlist import generate_shortlist
    from scoutiq.scoring.recruitment_fit import load_yaml
    req = load_yaml("requirements_example.yaml")
    save_shortlist(req, generate_shortlist(req))

    print("\nRows loaded:")
    with engine.connect() as conn:
        for t in ["leagues", "clubs", "players", "player_seasons", "metrics", "player_stats",
                  "market_values", "performance_scores", "score_components", "club_requirements", "shortlists"]:
            print(f"  {t:<20} {conn.execute(text(f'SELECT COUNT(*) FROM {t}')).scalar():>7}")


if __name__ == "__main__":
    load_all()