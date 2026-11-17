"""Player Comparison: radar chart of category scores plus key metrics side by side."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import (CATEGORY_LABELS, POSITION_NAMES, fmt_value, footer, metric_label, metric_values,
                    players_table, position_metrics, scoring_model)

st.set_page_config(page_title="Compare Players · ScoutIQ", page_icon="⚖️", layout="wide")
st.title("⚖️ Compare Players")
df = players_table()

default = df.sort_values("performance_score", ascending=False).query("position_group == 'ST'").label.head(2).tolist()
chosen = st.multiselect("Choose 2-4 players (ideally the same position)", df.sort_values("player_name").label.tolist(),
                        default=default, max_selections=4)
if len(chosen) < 2:
    st.info("Select at least two players to compare.")
    st.stop()

sel = df[df.label.isin(chosen)].set_index("label").loc[chosen].reset_index()
positions = sel.position_group.unique()
if len(positions) > 1:
    st.warning("These players play different positions. Percentiles are calculated within each player's own "
               "position, so the comparison is indicative only.")
pos = sel.position_group.iloc[0]
cats = list(scoring_model()["position_weights"][pos])

st.subheader(f"Category profile ({POSITION_NAMES[pos]} model)")
fig = go.Figure()
for r in sel.itertuples():
    values = [getattr(r, f"cat_{c}") for c in cats]
    fig.add_trace(go.Scatterpolar(r=values + values[:1], theta=[CATEGORY_LABELS[c] for c in cats] + [CATEGORY_LABELS[cats[0]]],
                                  fill="toself", name=r.player_name, opacity=0.6))
fig.update_layout(polar=dict(radialaxis=dict(range=[0, 100])), height=480)
st.plotly_chart(fig)

st.subheader("Scores and value")
summary = pd.DataFrame({
    "Player": sel.player_name, "Club": sel.team, "Age": sel.age.round(0), "Market value": sel.market_value_eur.map(fmt_value),
    "Minutes": sel.minutes.round(0), "Performance Score": sel.performance_score,
    "Rank in position": sel.rank_in_position.astype(int).astype(str) + " / " + sel.players_in_position.astype(int).astype(str),
})
st.dataframe(summary, hide_index=True)

st.subheader("Key metrics (per 90 or %, with percentile in brackets)")
vals = metric_values()
rows = []
for m in position_metrics(pos):
    row = {"Metric": metric_label(m)}
    for r in sel.itertuples():
        v = vals.at[r.player_season_id, m] if (r.player_season_id in vals.index and m in vals.columns) else None
        pc = getattr(r, f"pct_{m}", None)
        row[r.player_name] = "-" if pd.isna(v) else f"{v:.2f} ({pc:.0f})" if pd.notna(pc) else f"{v:.2f}"
    rows.append(row)
st.dataframe(pd.DataFrame(rows), hide_index=True)

footer()
