"""
Recruitment-Fit Evaluation Module.

Measures how well a player suits ONE club's recruitment brief. It is kept
independent of the Performance Score: a brilliant player can still be a poor
fit (wrong age, too expensive, wrong style).

Step 1 - hard filters (pass/fail, not scored):
    position, age range, maximum budget. Players with unknown age or market
    value are excluded, because the brief cannot be checked for them.
Step 2 - four components, each 0-100:
    style      average percentile across the chosen style's metrics
    attributes how well the player meets the club's minimum percentile targets
               (100 per target met; partial credit = percentile / target)
    age        100 inside the ideal age range, minus 20 per year outside it
    value      affordability: 100 - 50 x (market value / budget), so 50-100
Step 3 - Fit Score = weighted average of the components, using the club's own
    importance values. A text explanation is generated for every player.
"""
from __future__ import annotations

import pandas as pd
import yaml

from scoutiq.config import PROJECT_ROOT, load_settings
from scoutiq.features.player_stats import METRICS


def load_yaml(name: str) -> dict:
    with open(PROJECT_ROOT / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_scored_players() -> pd.DataFrame:
    """Performance scores joined with age and market value (one row per player-league)."""
    processed = PROJECT_ROOT / load_settings()["paths"]["processed"]
    scores = pd.read_parquet(processed / "performance_scores.parquet")
    profiles = pd.read_parquet(processed / "player_profiles.parquet")
    keep = ["player_id", "league", "age", "market_value_eur", "match_status"]
    return scores.merge(profiles[keep], on=["player_id", "league"], how="left")


def validate_requirements(req: dict, styles: dict) -> None:
    pos = req["position"]
    if pos not in styles:
        raise ValueError(f"Unknown position '{pos}'. Use one of {list(styles)}")
    if req["style"] not in styles[pos]:
        raise ValueError(f"Style '{req['style']}' not defined for {pos}. Options: {list(styles[pos])}")
    for m in req.get("attribute_targets", {}):
        if m not in METRICS:
            raise ValueError(f"Unknown metric in attribute_targets: {m}")
    if req["age_min"] > req["age_max"]:
        raise ValueError("age_min is greater than age_max")


def _age_score(age: float, ideal: tuple[float, float]) -> float:
    low, high = ideal
    gap = max(low - age, 0, age - high)
    return max(0.0, 100 - 20 * gap)


def evaluate_fit(players: pd.DataFrame, req: dict, styles: dict | None = None) -> pd.DataFrame:
    """Apply the hard filters, then score every remaining player against the brief."""
    styles = styles or load_yaml("style_profiles.yaml")
    validate_requirements(req, styles)

    df = players[
        (players.position_group == req["position"])
        & players.age.between(req["age_min"], req["age_max"])
        & (players.market_value_eur <= req["budget_max_eur"])
    ].copy()
    if df.empty:
        return df

    style_metrics = styles[req["position"]][req["style"]]
    targets = req.get("attribute_targets", {})
    ideal = tuple(req.get("ideal_age", [req["age_min"], req["age_max"]]))
    importance = req.get("importance", {"style": 40, "attributes": 30, "age": 15, "value": 15})
    if not targets:
        importance = {k: v for k, v in importance.items() if k != "attributes"}
    total_w = sum(importance.values())

    df["fit_style"] = df[[f"pct_{m}" for m in style_metrics]].mean(axis=1).round(1)
    if targets:
        parts = [(df[f"pct_{m}"].fillna(0) / t * 100).clip(upper=100) for m, t in targets.items()]
        df["fit_attributes"] = (sum(parts) / len(parts)).round(1)
        df["targets_met"] = sum((df[f"pct_{m}"].fillna(0) >= t).astype(int) for m, t in targets.items())
    df["fit_age"] = df.age.apply(lambda a: _age_score(a, ideal)).round(1)
    df["fit_value"] = (100 - 50 * df.market_value_eur / req["budget_max_eur"]).round(1)

    df["fit_score"] = (sum(df[f"fit_{k}"] * w for k, w in importance.items()) / total_w).round(1)
    df["fit_explanation"] = [explain(r, req, style_metrics, targets, ideal) for _, r in df.iterrows()]
    return df.sort_values(["fit_score", "performance_score"], ascending=False)


def explain(row: pd.Series, req: dict, style_metrics: list[str], targets: dict, ideal) -> str:
    """Plain-English reasons behind a player's Fit Score."""
    parts = [f"Style '{req['style']}': {row.fit_style:.0f}/100"]
    strongest = max(style_metrics, key=lambda m: row.get(f"pct_{m}", 0) or 0)
    parts[-1] += f" (best: {METRICS[strongest]['label']}, {row[f'pct_{strongest}']:.0f}th pct)"
    if targets:
        missed = [METRICS[m]["label"] for m, t in targets.items() if (row.get(f"pct_{m}") or 0) < t]
        parts.append(f"meets {int(row.targets_met)}/{len(targets)} targets"
                     + (f" (misses: {', '.join(missed)})" if missed else ""))
    parts.append(f"age {row.age:.0f} (ideal {ideal[0]}-{ideal[1]})")
    parts.append(f"value €{row.market_value_eur / 1e6:.1f}m of €{req['budget_max_eur'] / 1e6:.0f}m budget")
    return "; ".join(parts)