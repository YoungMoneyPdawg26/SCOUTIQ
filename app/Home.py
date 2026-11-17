"""ScoutIQ — Recruitment Dashboard (home page). Run with:  streamlit run app/Home.py"""
import plotly.express as px
import streamlit as st

from common import POSITION_ORDER, POSITION_NAMES, fmt_value, footer, players_table
from scoutiq.db import queries

st.set_page_config(page_title="ScoutIQ", page_icon="⚽", layout="wide")
st.title("⚽ ScoutIQ — Recruitment Dashboard")
st.caption("Data-driven player evaluation and recruitment decision support · Season 2015/16")

df = players_table()
summary = queries.dashboard_summary()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Players evaluated", f"{len(df):,}")
c2.metric("Shortlisted players", summary["shortlisted_players"], help=f"Across {summary['briefs']} saved club briefs")
c3.metric("Average Performance Score", f"{df.performance_score.mean():.1f}")
c4.metric("Market value known", f"{df.market_value_eur.notna().mean() * 100:.0f}%")

left, right = st.columns(2)
with left:
    st.subheader("Players by position")
    counts = (df.groupby("position_group").size().reindex(POSITION_ORDER).rename("players").reset_index())
    counts["position"] = counts.position_group.map(POSITION_NAMES)
    st.plotly_chart(px.bar(counts, x="position", y="players", text="players"))
with right:
    st.subheader("Market value by position")
    mv = df.dropna(subset=["market_value_eur"]).assign(value_m=lambda d: d.market_value_eur / 1e6)
    fig = px.box(mv, x="position_name", y="value_m", points=False,
                 category_orders={"position_name": [POSITION_NAMES[p] for p in POSITION_ORDER]},
                 labels={"value_m": "Market value (€m)", "position_name": ""})
    st.plotly_chart(fig)

st.subheader("Performance vs market value")
st.caption("Players in the top-left perform well for their price — potential value signings.")
fig = px.scatter(mv, x="value_m", y="performance_score", color="position_name", log_x=True,
                 hover_name="player_name", hover_data={"team": True, "age": True, "value_m": ":.1f"},
                 labels={"value_m": "Market value (€m, log scale)", "performance_score": "Performance Score",
                         "position_name": "Position"})
st.plotly_chart(fig)

st.subheader("Recruitment insights")
a, b = st.columns(2)
with a:
    st.markdown("**Top performer in each position**")
    top = df.sort_values("performance_score", ascending=False).groupby("position_group").head(1)
    top = top.set_index("position_group").reindex(POSITION_ORDER).reset_index()
    st.dataframe(top.assign(value=top.market_value_eur.map(fmt_value))[
        ["position_name", "player_name", "team", "performance_score", "value"]],
        hide_index=True)
with b:
    st.markdown("**Best value: top performer valued at €5m or less**")
    cheap = df[df.market_value_eur <= 5e6].sort_values("performance_score", ascending=False).groupby("position_group").head(1)
    cheap = cheap.set_index("position_group").reindex(POSITION_ORDER).dropna(subset=["player_name"]).reset_index()
    st.dataframe(cheap.assign(value=cheap.market_value_eur.map(fmt_value))[
        ["position_name", "player_name", "team", "performance_score", "value"]],
        hide_index=True)

footer()
