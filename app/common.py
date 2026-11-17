"""Shared helpers for the ScoutIQ Streamlit pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from scoutiq.cleaning.events import GROUP_NAMES
from scoutiq.db import queries
from scoutiq.features.player_stats import METRICS
from scoutiq.scoring.performance import load_model
from scoutiq.scoring.recruitment_fit import load_yaml

POSITION_ORDER = ["GK", "CB", "FB", "DM", "CM", "W", "ST"]
POSITION_NAMES = GROUP_NAMES                     # e.g. "CB" -> "Centre-Back"
CATEGORY_LABELS = {
    "finishing": "Finishing", "creation": "Chance creation", "progression": "Progression",
    "defending": "Defending", "aerial": "Aerial", "retention": "Ball retention",
    "shot_stopping": "Shot-stopping", "command": "Command of area", "distribution": "Distribution",
}


@st.cache_data(ttl=600, show_spinner="Loading players from MySQL ...")
def players_table() -> pd.DataFrame:
    df = queries.fit_input_players()
    df["position_name"] = df["position_group"].map(POSITION_NAMES)
    df["label"] = df["player_name"] + " — " + df["team"] + " (" + df["position_group"] + ")"
    return df


@st.cache_data(ttl=600)
def metric_values() -> pd.DataFrame:
    return queries.player_metric_values()


@st.cache_data
def scoring_model() -> dict:
    return load_model()


@st.cache_data
def style_profiles() -> dict:
    return load_yaml("style_profiles.yaml")


def position_metrics(position: str) -> list[str]:
    """Metrics used to score a position (from the categories it is weighted on)."""
    model = scoring_model()
    cats = model["position_weights"][position]
    seen = []
    for c in cats:
        for m in model["categories"][c]:
            if m not in seen:
                seen.append(m)
    return seen


def fmt_value(v) -> str:
    return "unknown" if pd.isna(v) else f"€{v / 1e6:.1f}m"


def fmt_age(v) -> str:
    return "unknown" if pd.isna(v) else f"{v:.0f}"


def metric_label(m: str) -> str:
    return METRICS[m]["label"]


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def footer() -> None:
    st.divider()
    st.caption(
        "Data: StatsBomb Open Data, 2015/16 Premier League, La Liga, Serie A and Ligue 1 "
        "(players with 900+ minutes). Market values and ages: Transfermarkt estimates via the "
        "Kaggle 'player-scores' dataset (CC0). Scores describe on-ball statistical output only "
        "and are a decision-support aid, not a complete judgement of a player."
    )