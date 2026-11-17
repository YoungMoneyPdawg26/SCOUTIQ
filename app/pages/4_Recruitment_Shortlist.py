"""Recruitment Shortlisting: define a club brief, generate a ranked shortlist with
explanations, and save it to MySQL."""
import pandas as pd
import streamlit as st

from common import (POSITION_NAMES, POSITION_ORDER, fmt_value, footer, metric_label, players_table,
                    position_metrics, style_profiles)
from scoutiq.db import queries
from scoutiq.ranking.shortlist import generate_shortlist
from scoutiq.scoring.recruitment_fit import evaluate_fit

st.set_page_config(page_title="Shortlist · ScoutIQ", page_icon="📋", layout="wide")
st.title("📋 Recruitment Shortlist")
st.caption("The Recruitment-Fit Score is separate from the Performance Score: it measures fit to THIS brief.")
df = players_table()

st.subheader("1. Club recruitment brief")
a, b, c = st.columns(3)
with a:
    club = st.text_input("Club name", "Example FC")
    position = st.selectbox("Position", POSITION_ORDER, index=1, format_func=POSITION_NAMES.get)
    styles = style_profiles()[position]
    style = st.selectbox("Playing style", list(styles), format_func=lambda s: s.replace("_", " ").title())
    st.caption("Style metrics: " + ", ".join(metric_label(m) for m in styles[style]))
with b:
    age_min, age_max = st.slider("Age range (hard limit)", 16, 40, (20, 28))
    ideal = st.slider("Ideal age (full age score)", age_min, age_max, (min(max(22, age_min), age_max), min(26, age_max)))
    budget_m = st.number_input("Maximum budget (€m)", min_value=0.5, max_value=150.0, value=15.0, step=0.5)
    top_n = st.slider("Shortlist size", 3, 25, 10)
with c:
    st.markdown("**Importance of each part of the fit**")
    w_style = st.slider("Playing style", 0, 100, 40)
    w_attr = st.slider("Attribute targets", 0, 100, 30)
    w_age = st.slider("Age", 0, 100, 15)
    w_value = st.slider("Value for money", 0, 100, 15)

st.markdown("**Attribute targets** — minimum percentile within the position")
options = [m for m in position_metrics(position) if f"pct_{m}" in df.columns]
picked = st.multiselect("Required attributes", options, format_func=metric_label)
targets = {}
cols = st.columns(max(1, min(4, len(picked)))) if picked else []
for i, m in enumerate(picked):
    targets[m] = cols[i % len(cols)].slider(metric_label(m), 0, 100, 60, key=f"t_{m}")

req = {
    "club_name": club, "position": position, "style": style, "age_min": age_min, "age_max": age_max,
    "ideal_age": list(ideal), "budget_max_eur": budget_m * 1e6, "attribute_targets": targets,
    "importance": {"style": w_style, "attributes": w_attr, "age": w_age, "value": w_value},
}

if sum(req["importance"].values()) == 0:
    st.error("Set at least one importance slider above 0.")
    st.stop()

if st.button("Generate shortlist", type="primary"):
    st.session_state["req"] = req
    st.session_state["shortlist"] = generate_shortlist(req, top_n=top_n, players=df)
    st.session_state["passing"] = len(evaluate_fit(df, req))

if "shortlist" in st.session_state:
    sl, used = st.session_state["shortlist"], st.session_state["req"]
    st.subheader("2. Ranked shortlist")
    st.write(f"**{st.session_state['passing']}** players passed the hard filters "
             f"(position, age {used['age_min']}-{used['age_max']}, value ≤ €{used['budget_max_eur'] / 1e6:.1f}m). "
             "Players with unknown age or value are excluded.")
    if sl.empty:
        st.warning("No players meet this brief. Try widening the age range or increasing the budget.")
    else:
        view = sl.assign(value=sl.market_value_eur.map(fmt_value), age_=sl.age.round(0))
        cols = ["shortlist_rank", "player_name", "team", "age_", "value", "performance_score", "fit_score",
                "fit_style"] + (["fit_attributes"] if "fit_attributes" in sl.columns else []) + ["fit_age", "fit_value"]
        st.dataframe(view[cols].rename(columns={
            "shortlist_rank": "#", "player_name": "Player", "team": "Club", "age_": "Age", "value": "Value",
            "performance_score": "Performance", "fit_score": "FIT SCORE", "fit_style": "Style",
            "fit_attributes": "Targets", "fit_age": "Age fit", "fit_value": "Value fit"}),
            hide_index=True)
        st.markdown("**Why each player is recommended**")
        for r in sl.itertuples():
            st.markdown(f"{r.shortlist_rank}. **{r.player_name}** ({r.team}): {r.fit_explanation}")
        if st.button("💾 Save shortlist to database"):
            rid = queries.save_shortlist(used, sl)
            st.cache_data.clear()
            st.success(f"Saved as brief #{rid}.")

with st.expander("Saved shortlists"):
    saved = queries.saved_shortlists()
    if saved.empty:
        st.write("None yet.")
    else:
        st.dataframe(saved.drop(columns=["explanation"]), hide_index=True)

footer()