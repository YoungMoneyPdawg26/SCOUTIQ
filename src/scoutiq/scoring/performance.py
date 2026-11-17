"""
Player Performance Evaluation Module.

Calculates each player's Performance Score (0-100) relative to players in the
SAME POSITION GROUP, using the model in config/scoring.yaml:

  1. Eligibility   - only players with at least `min_minutes` are scored.
  2. Normalisation - every metric becomes a percentile rank (0-100) within the
                     position group. For metrics where lower is better
                     (e.g. turnovers) the ranking is reversed.
  3. Categories    - a category score is the average percentile of its metrics.
  4. Score         - weighted average of category scores using the position's
                     weights. If a category has no data for a player, the
                     remaining weights are rescaled so they still add to 100%.

Usage:
    python -m scoutiq.scoring.performance
Output:
    data/processed/performance_scores.parquet (+ .csv)
"""
from __future__ import annotations

import pandas as pd
import yaml

from scoutiq.config import PROJECT_ROOT, load_settings
from scoutiq.features.player_stats import METRICS


def load_model() -> dict:
    """Read config/scoring.yaml and check the weights are valid."""
    with open(PROJECT_ROOT / "config" / "scoring.yaml", encoding="utf-8") as f:
        model = yaml.safe_load(f)
    for pos, weights in model["position_weights"].items():
        total = sum(weights.values())
        if total != 100:
            raise ValueError(f"Weights for {pos} add up to {total}, not 100")
        unknown = set(weights) - set(model["categories"])
        if unknown:
            raise ValueError(f"{pos} uses unknown categories: {unknown}")
    return model


def metric_column(metric: str) -> str:
    """Count metrics are compared per 90 minutes; ratio metrics as they are."""
    return metric if METRICS[metric]["kind"] == "ratio" else f"{metric}_p90"


def percentile_ranks(df: pd.DataFrame, metrics: list[str], group_col: str = "position_group") -> pd.DataFrame:
    """Percentile (0-100) of each metric within each position group.
    100 = best in the group. Missing values stay missing."""
    out = pd.DataFrame(index=df.index)
    for m in metrics:
        out[f"pct_{m}"] = (
            df.groupby(group_col)[metric_column(m)]
            .rank(pct=True, method="average", ascending=METRICS[m]["higher_is_better"])
            .mul(100).round(1)
        )
    return out


def score_players(stats: pd.DataFrame, model: dict) -> pd.DataFrame:
    """Return eligible players with percentiles, category scores and Performance Score."""
    df = stats[(stats.minutes >= model["min_minutes"]) & stats.position_group.notna()].copy()

    all_metrics = sorted({m for ms in model["categories"].values() for m in ms})
    df = df.join(percentile_ranks(df, all_metrics))

    for cat, metrics in model["categories"].items():
        df[f"cat_{cat}"] = df[[f"pct_{m}" for m in metrics]].mean(axis=1, skipna=True).round(1)

    scores = []
    for _, row in df.iterrows():
        weights = model["position_weights"][row.position_group]
        available = {c: w for c, w in weights.items() if pd.notna(row[f"cat_{c}"])}
        total_w = sum(available.values())
        score = sum(row[f"cat_{c}"] * w for c, w in available.items()) / total_w if total_w else float("nan")
        scores.append(round(score, 1))
    df["performance_score"] = scores
    df["model_version"] = model["model_version"]
    df["rank_in_position"] = (
        df.groupby("position_group")["performance_score"].rank(ascending=False, method="min").astype(int)
    )
    df["players_in_position"] = df.groupby("position_group")["player_id"].transform("count")
    return df.sort_values(["position_group", "rank_in_position"])


def main() -> None:
    settings = load_settings()
    processed = PROJECT_ROOT / settings["paths"]["processed"]
    stats = pd.read_parquet(processed / "player_season_stats.parquet")
    model = load_model()

    scored = score_players(stats, model)
    scored.to_parquet(processed / "performance_scores.parquet", index=False)
    scored.to_csv(processed / "performance_scores.csv", index=False)

    print(f"Scored {len(scored)} players (min {model['min_minutes']} minutes, model {model['model_version']})")
    for pos, group in scored.groupby("position_name"):
        print(f"\nTop 5 {pos}s:")
        print(group.head(5)[["rank_in_position", "player_name", "team", "minutes", "performance_score"]]
              .to_string(index=False))


if __name__ == "__main__":
    main()