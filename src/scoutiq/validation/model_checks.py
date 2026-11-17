"""
Testing & Validation Module — evidence that the scoring model is meaningful.

Checks (results printed and saved to docs/validation_report.md):
  1. Score sanity      - scores lie in 0-100; distribution per position.
  2. Weight sensitivity - every category weight is moved by +/-10 points (other
                         weights rescaled so the total stays 100). We measure how
                         much the ranking changes (Spearman rank correlation with
                         the original ranking) and how many of the original top 10
                         stay in the top 10. High values = rankings are robust.
  3. Minutes bias      - does playing fewer minutes inflate scores? Spearman
                         correlation between minutes and score, and the average
                         score of 900-1350 minute players vs 1350+.
  4. External check    - Spearman correlation between Performance Score and
                         Transfermarkt market value. The market is an independent
                         opinion of quality, so a positive correlation suggests the
                         model captures real ability (it should not be near 1:
                         price also reflects age, contract and reputation).

Usage:
    python -m scoutiq.validation.model_checks
"""
from __future__ import annotations

import pandas as pd

from scoutiq.config import PROJECT_ROOT, load_settings
from scoutiq.scoring.performance import load_model

STEP = 10  # weight change in percentage points


def score_with_weights(df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Recompute Performance Scores from stored category scores with new weights."""
    num = sum(df[f"cat_{c}"].fillna(0) * w for c, w in weights.items())
    den = sum(df[f"cat_{c}"].notna() * w for c, w in weights.items())
    return num / den.where(den > 0)


def perturb(weights: dict[str, float], category: str, delta: float) -> dict[str, float] | None:
    """Move one weight by delta; rescale the others so the total stays 100."""
    new_w = weights[category] + delta
    if new_w < 0 or new_w > 100:
        return None
    others = {c: w for c, w in weights.items() if c != category}
    rest = sum(others.values())
    if rest == 0:
        return None
    scale = (100 - new_w) / rest
    return {**{c: w * scale for c, w in others.items()}, category: new_w}


def sensitivity(df: pd.DataFrame, model: dict) -> pd.DataFrame:
    rows = []
    for pos, weights in model["position_weights"].items():
        g = df[df.position_group == pos]
        if len(g) < 10:
            continue
        base = score_with_weights(g, weights)
        base_top = set(base.nlargest(10).index)
        for cat in weights:
            for delta in (-STEP, STEP):
                w = perturb(weights, cat, delta)
                if w is None:
                    continue
                new = score_with_weights(g, w)
                rows.append({
                    "position": pos, "category": cat, "change": f"{delta:+d}",
                    "spearman": base.corr(new, method="spearman"),
                    "top10_kept": len(base_top & set(new.nlargest(10).index)),
                })
    return pd.DataFrame(rows)


def minutes_bias(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for pos, g in df.groupby("position_group"):
        low, high = g[g.minutes < 1350], g[g.minutes >= 1350]
        rows.append({
            "position": pos, "players": len(g),
            "spearman_minutes_vs_score": g.minutes.corr(g.performance_score, method="spearman"),
            "mean_score_900_1350": low.performance_score.mean(),
            "mean_score_1350_plus": high.performance_score.mean(),
            "share_of_top10_under_1350": (g.nlargest(10, "performance_score").minutes < 1350).mean(),
        })
    return pd.DataFrame(rows)


def market_value_check(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for pos, g in df.dropna(subset=["market_value_eur"]).groupby("position_group"):
        rows.append({"position": pos, "players_with_value": len(g),
                     "spearman_score_vs_value": g.performance_score.corr(g.market_value_eur, method="spearman")})
    return pd.DataFrame(rows)


def run(df: pd.DataFrame, model: dict) -> dict[str, pd.DataFrame]:
    sanity = df.groupby("position_group").performance_score.agg(["count", "min", "mean", "max"]).reset_index()
    sens = sensitivity(df, model)
    sens_summary = (sens.groupby("position")
                    .agg(min_spearman=("spearman", "min"), mean_spearman=("spearman", "mean"),
                         min_top10_kept=("top10_kept", "min"), mean_top10_kept=("top10_kept", "mean"))
                    .reset_index())
    return {"score_sanity": sanity, "sensitivity_summary": sens_summary, "sensitivity_detail": sens,
            "minutes_bias": minutes_bias(df), "market_value_check": market_value_check(df)}


def main() -> None:
    processed = PROJECT_ROOT / load_settings()["paths"]["processed"]
    scores = pd.read_parquet(processed / "performance_scores.parquet")
    profiles = pd.read_parquet(processed / "player_profiles.parquet")
    df = scores.merge(profiles[["player_id", "league", "market_value_eur"]], on=["player_id", "league"], how="left")
    model = load_model()

    results = run(df, model)
    assert results["score_sanity"]["min"].min() >= 0 and results["score_sanity"]["max"].max() <= 100

    lines = ["# ScoutIQ — Model Validation Report", "",
             f"Model {model['model_version']} · {len(df)} scored players · weight change ±{STEP} points", ""]
    for name, table in results.items():
        if name == "sensitivity_detail":
            continue
        print(f"\n=== {name.replace('_', ' ').title()} ===")
        print(table.round(3).to_string(index=False))
        lines += [f"## {name.replace('_', ' ').title()}", "", table.round(3).to_markdown(index=False), ""]
    out = PROJECT_ROOT / "docs" / "validation_report.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    results["sensitivity_detail"].round(3).to_csv(PROJECT_ROOT / "docs" / "sensitivity_detail.csv", index=False)
    print(f"\nSaved {out} and docs/sensitivity_detail.csv")


if __name__ == "__main__":
    main()