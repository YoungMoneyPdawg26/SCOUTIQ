"""
Player Ranking and Shortlisting Module.

Builds a ranked shortlist for a club brief: players are ranked by
Recruitment-Fit Score, with the (independent) Performance Score shown
alongside so the two are never silently merged.

Usage:
    python -m scoutiq.ranking.shortlist                        # uses config/requirements_example.yaml
    python -m scoutiq.ranking.shortlist path/to/brief.yaml
"""
from __future__ import annotations

import sys

import pandas as pd
import yaml

from scoutiq.scoring.recruitment_fit import evaluate_fit, load_scored_players, load_yaml

SHORTLIST_COLUMNS = [
    "player_name", "team", "league", "age", "market_value_eur", "minutes",
    "performance_score", "fit_score", "fit_style", "fit_attributes", "fit_age",
    "fit_value", "fit_explanation",
]


def generate_shortlist(req: dict, top_n: int = 10, players: pd.DataFrame | None = None) -> pd.DataFrame:
    players = load_scored_players() if players is None else players
    fit = evaluate_fit(players, req)
    if fit.empty:
        return fit
    out = fit.head(top_n).copy()
    out.insert(0, "shortlist_rank", range(1, len(out) + 1))
    return out[["shortlist_rank", "player_id"] + [c for c in SHORTLIST_COLUMNS if c in out.columns]]


def main() -> None:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            req = yaml.safe_load(f)
    else:
        req = load_yaml("requirements_example.yaml")

    players = load_scored_players()
    eligible = len(evaluate_fit(players, req))
    shortlist = generate_shortlist(req, players=players)
    print(f"Brief: {req['club_name']} | {req['position']} ({req['style']}) | "
          f"age {req['age_min']}-{req['age_max']} | budget €{req['budget_max_eur'] / 1e6:.0f}m")
    print(f"Players passing the hard filters: {eligible}\n")
    if shortlist.empty:
        print("No players meet this brief. Try widening the age range or budget.")
        return
    view = shortlist.copy()
    view["market_value_eur"] = (view.market_value_eur / 1e6).round(1).astype(str) + "m"
    print(view[["shortlist_rank", "player_name", "team", "age", "market_value_eur",
                "performance_score", "fit_score"]].to_string(index=False))
    print("\nWhy the top 3:")
    for r in shortlist.head(3).itertuples():
        print(f"  {r.shortlist_rank}. {r.player_name}: {r.fit_explanation}")


if __name__ == "__main__":
    main()