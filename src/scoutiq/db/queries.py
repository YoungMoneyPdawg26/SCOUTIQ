"""
Database query functions used by the Streamlit app.
Every query uses bound parameters (:name) — never string formatting —
so user input can't break or inject SQL.
"""
from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import text

from scoutiq.db.connection import get_engine


def search_players(position: str | None = None, leagues: list[str] | None = None,
                   age_range: tuple[float, float] | None = None,
                   max_value_eur: float | None = None, min_score: float = 0,
                   club: str | None = None) -> pd.DataFrame:
    """Scored players matching the filters, best Performance Score first."""
    sql = "SELECT * FROM v_player_overview WHERE performance_score IS NOT NULL AND performance_score >= :min_score"
    params: dict = {"min_score": min_score}
    if position:
        sql += " AND position_group = :position"
        params["position"] = position
    if leagues:
        names = ", ".join(f":lg{i}" for i in range(len(leagues)))
        sql += f" AND league IN ({names})"
        params.update({f"lg{i}": lg for i, lg in enumerate(leagues)})
    if age_range:
        sql += " AND age BETWEEN :age_lo AND :age_hi"
        params.update(age_lo=age_range[0], age_hi=age_range[1])
    if max_value_eur is not None:
        sql += " AND market_value_eur <= :max_value"
        params["max_value"] = max_value_eur
    if club:
        sql += " AND club LIKE :club"
        params["club"] = f"%{club}%"
    sql += " ORDER BY performance_score DESC"
    return pd.read_sql(text(sql), get_engine(), params=params)


def player_profile(player_season_id: int) -> dict:
    """Everything needed for one player's profile page."""
    eng = get_engine()
    p = {"player_season_id": player_season_id}
    overview = pd.read_sql(text("SELECT * FROM v_player_overview WHERE player_season_id = :player_season_id"), eng, params=p)
    stats = pd.read_sql(text("""
        SELECT m.metric_code, m.label, m.category, m.kind, m.data_status, m.higher_is_better,
               s.total, s.per90, s.ratio_value
        FROM player_stats s JOIN metrics m ON m.metric_code = s.metric_code
        WHERE s.player_season_id = :player_season_id"""), eng, params=p)
    components = pd.read_sql(text("""
        SELECT component_type, component_code, score FROM score_components
        WHERE player_season_id = :player_season_id"""), eng, params=p)
    return {"overview": overview, "stats": stats, "components": components}


def save_shortlist(req: dict, shortlist: pd.DataFrame) -> int:
    """Store a club brief and its shortlist. Returns the new requirement_id."""
    eng = get_engine()
    ideal = req.get("ideal_age", [req["age_min"], req["age_max"]])
    with eng.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO club_requirements (club_name, position_group, style, age_min, age_max,
                ideal_age_min, ideal_age_max, budget_max_eur, attribute_targets, importance)
            VALUES (:club, :pos, :style, :amin, :amax, :imin, :imax, :budget, :targets, :importance)"""),
            dict(club=req["club_name"], pos=req["position"], style=req["style"], amin=req["age_min"],
                 amax=req["age_max"], imin=ideal[0], imax=ideal[1], budget=req["budget_max_eur"],
                 targets=json.dumps(req.get("attribute_targets", {})),
                 importance=json.dumps(req.get("importance", {}))))
        req_id = result.lastrowid
        if shortlist is None or shortlist.empty:
            return req_id
        ids = pd.read_sql(text("SELECT player_season_id, player_id, league_id FROM player_seasons"), conn)
        sl = shortlist.merge(ids, left_on=["player_id", "league"], right_on=["player_id", "league_id"])
        for r in sl.itertuples():
            conn.execute(text("""
                INSERT INTO shortlists (requirement_id, player_season_id, shortlist_rank, fit_score,
                    fit_style, fit_attributes, fit_age, fit_value, explanation)
                VALUES (:rid, :psid, :rank, :fit, :fs, :fa, :fage, :fv, :expl)"""),
                dict(rid=req_id, psid=int(r.player_season_id), rank=int(r.shortlist_rank),
                     fit=float(r.fit_score), fs=float(r.fit_style),
                     fa=float(r.fit_attributes) if pd.notna(getattr(r, "fit_attributes", None)) else None,
                     fage=float(r.fit_age), fv=float(r.fit_value), expl=r.fit_explanation))
    return req_id


def saved_shortlists() -> pd.DataFrame:
    sql = """SELECT r.requirement_id, r.club_name, r.position_group, r.style, r.created_at,
                    s.shortlist_rank, o.player_name, o.club, o.age, o.market_value_eur,
                    o.performance_score, s.fit_score, s.status, s.explanation
             FROM club_requirements r
             JOIN shortlists s ON s.requirement_id = r.requirement_id
             JOIN v_player_overview o ON o.player_season_id = s.player_season_id
             ORDER BY r.requirement_id DESC, s.shortlist_rank"""
    return pd.read_sql(text(sql), get_engine())



# ---------- used by the Streamlit app ----------

TEXT_COLUMNS = {"player_name", "nickname", "nationality", "club", "league", "league_id",
                "position_group", "model_version"}


def _numeric(df: pd.DataFrame) -> pd.DataFrame:
    """MySQL DECIMAL values arrive as Python Decimals; convert them to floats."""
    for c in df.columns:
        if c not in TEXT_COLUMNS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def fit_input_players() -> pd.DataFrame:
    """All scored players with their category scores (cat_*) and metric percentiles (pct_*),
    in the shape expected by scoutiq.scoring.recruitment_fit."""
    eng = get_engine()
    base = _numeric(pd.read_sql(text("""
        SELECT o.*, ps.league_id FROM v_player_overview o
        JOIN player_seasons ps ON ps.player_season_id = o.player_season_id
        WHERE o.performance_score IS NOT NULL"""), eng))
    comps = pd.read_sql(text("SELECT player_season_id, component_type, component_code, score FROM score_components"), eng)
    comps["score"] = pd.to_numeric(comps["score"], errors="coerce")
    comps["col"] = comps["component_type"].map({"category": "cat_", "metric": "pct_"}) + comps["component_code"]
    wide = comps.pivot_table(index="player_season_id", columns="col", values="score", aggfunc="first").reset_index()
    df = base.merge(wide, on="player_season_id", how="left")
    return df.rename(columns={"league": "league_name", "league_id": "league", "club": "team"})


def player_metric_values() -> pd.DataFrame:
    """Per-90 (or %) value of every metric for every scored player, one column per metric."""
    raw = pd.read_sql(text("""
        SELECT s.player_season_id, s.metric_code, COALESCE(s.per90, s.ratio_value) AS value
        FROM player_stats s JOIN performance_scores p ON p.player_season_id = s.player_season_id"""), get_engine())
    raw["value"] = pd.to_numeric(raw["value"], errors="coerce")
    return raw.pivot_table(index="player_season_id", columns="metric_code", values="value", aggfunc="first")


def dashboard_summary() -> dict:
    with get_engine().connect() as conn:
        return {
            "shortlisted_players": conn.execute(text("SELECT COUNT(DISTINCT player_season_id) FROM shortlists")).scalar(),
            "briefs": conn.execute(text("SELECT COUNT(*) FROM club_requirements")).scalar(),
        }