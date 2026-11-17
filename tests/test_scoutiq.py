"""
Automated tests for ScoutIQ's core logic. Run with:  pytest -v
They use small made-up datasets, so they run in seconds and need no database.
"""
import numpy as np
import pandas as pd
import pytest

from scoutiq.cleaning.events import parse_clock, player_stints
from scoutiq.cleaning.player_matching import name_score, normalise
from scoutiq.features.player_stats import METRICS, count_match_events, is_progressive
from scoutiq.scoring.performance import load_model, percentile_ranks, score_players
from scoutiq.scoring.recruitment_fit import evaluate_fit, load_yaml
from scoutiq.validation.model_checks import perturb


# ---------- helpers ----------

def fake_stats(n=60, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "player_id": range(n), "player_name": [f"P{i}" for i in range(n)], "team": "T", "league": "la_liga",
        "minutes": rng.integers(300, 3400, n), "position_group": rng.choice(["GK", "CB", "FB", "DM", "CM", "W", "ST"], n),
    })
    for m, meta in METRICS.items():
        df[m if meta["kind"] == "ratio" else f"{m}_p90"] = rng.random(n)
    return df


# ---------- data cleaning ----------

def test_parse_clock():
    assert parse_clock("45:30") == 45.5
    assert parse_clock("00:00") == 0


def test_player_stints_minutes_and_unused_subs():
    lineups = [{"team_name": "A", "lineup": [
        {"player_id": 1, "player_name": "Starter", "positions": [{"position": "Center Back", "from": "00:00", "to": None}]},
        {"player_id": 2, "player_name": "Sub", "positions": [{"position": "Right Wing", "from": "60:00", "to": None}]},
        {"player_id": 3, "player_name": "Unused", "positions": []},
    ]}]
    rows = {r["player_id"]: r for r in player_stints(lineups, end_minute=94)}
    assert rows[1]["minutes"] == 94 and rows[1]["position_group"] == "CB"
    assert rows[2]["minutes"] == 34 and rows[2]["position_group"] == "W"
    assert 3 not in rows  # unused substitutes get no minutes


# ---------- feature engineering ----------

def test_progressive_pass_rule():
    forward = {"location": [60, 40], "pass": {"end_location": [100, 40]}}
    own_half = {"location": [30, 40], "pass": {"end_location": [70, 40]}}
    failed = {"location": [60, 40], "pass": {"end_location": [100, 40], "outcome": {"name": "Incomplete"}}}
    corner = {"location": [119, 1], "pass": {"end_location": [110, 40], "type": {"name": "Corner"}}}
    assert is_progressive(forward)
    assert not is_progressive(own_half)
    assert not is_progressive(failed)
    assert not is_progressive(corner)


def test_penalties_excluded_and_xa_credited():
    from collections import defaultdict
    events = [
        {"id": "p", "type": {"name": "Pass"}, "player": {"id": 2}, "location": [60, 40],
         "pass": {"end_location": [100, 40], "shot_assist": True}},
        {"id": "s1", "type": {"name": "Shot"}, "player": {"id": 1},
         "shot": {"statsbomb_xg": 0.3, "outcome": {"name": "Goal"}, "key_pass_id": "p", "type": {"name": "Open Play"}}},
        {"id": "s2", "type": {"name": "Shot"}, "player": {"id": 1},
         "shot": {"statsbomb_xg": 0.76, "outcome": {"name": "Goal"}, "type": {"name": "Penalty"}}},
    ]
    totals = defaultdict(lambda: defaultdict(float))
    count_match_events(events, totals)
    assert totals[1]["goals"] == 1 and totals[1]["npxg"] == pytest.approx(0.3)
    assert totals[2]["xa"] == pytest.approx(0.3) and totals[2]["key_passes"] == 1


# ---------- performance score ----------

def test_weights_add_to_100():
    for pos, w in load_model()["position_weights"].items():
        assert sum(w.values()) == 100, pos


def test_percentiles_bounded_and_lower_is_better_reversed():
    df = fake_stats()
    pct = percentile_ranks(df, ["tackles", "turnovers"])
    assert pct.min().min() > 0 and pct.max().max() <= 100
    for _, g in df.groupby("position_group"):
        best = g.turnovers_p90.idxmin()
        assert pct.loc[best, "pct_turnovers"] == pct.loc[g.index, "pct_turnovers"].max()


def test_scores_in_range_and_minutes_threshold():
    scored = score_players(fake_stats(), load_model())
    assert scored.performance_score.between(0, 100).all()
    assert (scored.minutes >= 900).all()
    assert scored.performance_score.notna().all()


# ---------- recruitment fit ----------

def scored_players(n=80, seed=1):
    rng = np.random.default_rng(seed)
    d = pd.DataFrame({
        "player_id": range(n), "player_name": [f"P{i}" for i in range(n)], "team": "T", "league": "la_liga",
        "minutes": 2000, "position_group": ["CB"] * 60 + ["ST"] * 20,
        "performance_score": rng.uniform(30, 90, n), "age": rng.uniform(17, 35, n),
        "market_value_eur": rng.choice([1e6, 5e6, 12e6, 30e6, np.nan], n),
    })
    for m in METRICS:
        d[f"pct_{m}"] = rng.uniform(0, 100, n)
    return d


def test_fit_hard_filters():
    req = load_yaml("requirements_example.yaml")
    fit = evaluate_fit(scored_players(), req)
    assert (fit.position_group == req["position"]).all()
    assert fit.age.between(req["age_min"], req["age_max"]).all()
    assert (fit.market_value_eur <= req["budget_max_eur"]).all()
    assert fit.fit_score.between(0, 100).all()
    assert fit.fit_explanation.str.len().gt(0).all()


def test_fit_is_independent_of_performance_score():
    req = load_yaml("requirements_example.yaml")
    players = scored_players()
    a = evaluate_fit(players, req)
    players2 = players.assign(performance_score=100 - players.performance_score)  # flip performance
    b = evaluate_fit(players2, req)
    pd.testing.assert_series_equal(a.fit_score.sort_index(), b.fit_score.sort_index())


def test_impossible_brief_returns_empty():
    req = {**load_yaml("requirements_example.yaml"), "budget_max_eur": 1}
    assert evaluate_fit(scored_players(), req).empty


# ---------- player matching ----------

def test_name_normalisation_and_matching():
    assert normalise("Marek Hamšík") == "marek hamsik"
    exact = name_score(["Lionel Andrés Messi Cuccittini", "Lionel Messi"], "Lionel Messi")
    other = name_score(["Lionel Andrés Messi Cuccittini", "Lionel Messi"], "Cristiano Ronaldo")
    assert exact > 1.4 and other < 0.85


# ---------- validation helpers ----------

def test_perturb_keeps_total_100():
    w = {"finishing": 40, "creation": 20, "progression": 10, "defending": 10, "aerial": 10, "retention": 10}
    new = perturb(w, "finishing", 10)
    assert new["finishing"] == 50 and sum(new.values()) == pytest.approx(100)