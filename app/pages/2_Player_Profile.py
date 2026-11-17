"""Individual Player Profile: information, performance breakdown, strengths/weaknesses,
comparison with positional peers and recruitment (style) suitability."""
import pandas as pd
import plotly.express as px
import streamlit as st

from common import (CATEGORY_LABELS, POSITION_NAMES, fmt_age, fmt_value, footer, metric_label,
                    ordinal, players_table, position_metrics, scoring_model, style_profiles)
from scoutiq.db import queries

st.set_page_config(page_title="Player Profile · ScoutIQ", page_icon="👤", layout="wide")
df = players_table().sort_values("player_name")

labels = df.label.tolist()
default_id = st.session_state.get("player_season_id")
default_idx = int(df.reset_index(drop=True).index[df.player_season_id.values == default_id][0]) if default_id in df.player_season_id.values else 0
choice = st.selectbox("Player", labels, index=default_idx)
p = df[df.label == choice].iloc[0]
st.session_state["player_season_id"] = int(p.player_season_id)
pos = p.position_group

st.title(f"👤 {p.player_name}")
c = st.columns(6)
c[0].metric("Club", p.team)
c[1].metric("League", p.league_name)
c[2].metric("Position", POSITION_NAMES[pos])
c[3].metric("Age (Jan 2016)", fmt_age(p.age))
c[4].metric("Market value", fmt_value(p.market_value_eur))
c[5].metric("Minutes", f"{p.minutes:,.0f}")

st.subheader("Performance Score")
s1, s2 = st.columns([1, 3])
with s1:
    st.metric("Performance Score", f"{p.performance_score:.1f} / 100")
    st.write(f"Ranked **{ordinal(int(p.rank_in_position))}** of {int(p.players_in_position)} "
             f"{POSITION_NAMES[pos].lower()}s")
    st.caption(f"Model {p.model_version}: percentiles within position, weighted by category.")
with s2:
    weights = scoring_model()["position_weights"][pos]
    cats = pd.DataFrame([{"Category": f"{CATEGORY_LABELS[k]} ({w}%)", "Score": p[f"cat_{k}"]} for k, w in weights.items()])
    fig = px.bar(cats, x="Score", y="Category", orientation="h", range_x=[0, 100], text="Score")
    fig.update_layout(height=260, margin=dict(l=0, r=0, t=10, b=0), yaxis_title=None)
    st.plotly_chart(fig)

metrics = position_metrics(pos)
pct = pd.DataFrame({"metric": metrics, "Metric": [metric_label(m) for m in metrics],
                    "Percentile": [p.get(f"pct_{m}") for m in metrics]}).dropna()
st.subheader("Strengths and weaknesses")
st.caption(f"Percentile vs all {POSITION_NAMES[pos].lower()}s with 900+ minutes (100 = best).")
a, b = st.columns(2)
with a:
    st.markdown("**Strengths** (70th percentile or above)")
    strong = pct[pct.Percentile >= 70].nlargest(5, "Percentile")
    st.write("\n".join(f"- {r.Metric}: {ordinal(int(r.Percentile))} pct" for r in strong.itertuples()) or "None above the 70th percentile.")
with b:
    st.markdown("**Weaknesses** (30th percentile or below)")
    weak = pct[pct.Percentile <= 30].nsmallest(5, "Percentile")
    st.write("\n".join(f"- {r.Metric}: {ordinal(int(r.Percentile))} pct" for r in weak.itertuples()) or "None below the 30th percentile.")

st.subheader("Comparison with positional peers")
fig = px.bar(pct.sort_values("Percentile"), x="Percentile", y="Metric", orientation="h", range_x=[0, 100])
fig.add_vline(x=50, line_dash="dash", annotation_text="median")
fig.update_layout(height=max(300, 28 * len(pct)), margin=dict(l=0, r=0, t=10, b=0), yaxis_title=None)
st.plotly_chart(fig)

st.subheader("Recruitment suitability by playing style")
styles = style_profiles()[pos]
rows = [{"Style": s.replace("_", " ").title(),
         "Style match (0-100)": round(pd.Series([p.get(f"pct_{m}") for m in ms]).mean(), 1),
         "Based on": ", ".join(metric_label(m) for m in ms)} for s, ms in styles.items()]
st.dataframe(pd.DataFrame(rows).sort_values("Style match (0-100)", ascending=False), hide_index=True)
st.caption("For a full Recruitment-Fit Score against a club's age, budget and targets, use the Shortlist page.")

with st.expander("All statistics (season totals, per 90 and data status)"):
    stats = queries.player_profile(int(p.player_season_id))["stats"]
    for col in ["total", "per90", "ratio_value"]:
        stats[col] = pd.to_numeric(stats[col], errors="coerce")
    stats["Per 90 / %"] = stats.per90.fillna(stats.ratio_value).round(2)
    st.dataframe(stats[["label", "category", "total", "Per 90 / %", "data_status"]].rename(columns={
        "label": "Metric", "category": "Category", "total": "Season total", "data_status": "Data status"}),
        hide_index=True)
    st.caption("actual = counted directly from StatsBomb events · derived = calculated by ScoutIQ with a documented rule.")

footer()