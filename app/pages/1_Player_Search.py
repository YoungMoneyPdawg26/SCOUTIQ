"""Player Search: filter scored players by position, age, league, club, value and score."""
import streamlit as st

from common import POSITION_NAMES, POSITION_ORDER, fmt_value, footer, players_table

st.set_page_config(page_title="Player Search · ScoutIQ", page_icon="🔎", layout="wide")
st.title("🔎 Player Search")
df = players_table()

with st.sidebar:
    st.header("Filters")
    positions = st.multiselect("Position", POSITION_ORDER, format_func=POSITION_NAMES.get)
    leagues = st.multiselect("League", sorted(df.league_name.unique()))
    age = st.slider("Age", 16, 41, (16, 41))
    include_unknown = st.checkbox("Include players with unknown age/value", value=True)
    max_value = st.slider("Maximum market value (€m)", 0, 120, 120, step=1)
    min_score = st.slider("Minimum Performance Score", 0, 100, 0)
    club = st.text_input("Club contains")

res = df[df.performance_score >= min_score]
if positions:
    res = res[res.position_group.isin(positions)]
if leagues:
    res = res[res.league_name.isin(leagues)]
if club:
    res = res[res.team.str.contains(club, case=False, na=False)]
known = res.age.between(*age) & (res.market_value_eur <= max_value * 1e6)
unknown = res.age.isna() | res.market_value_eur.isna()
res = res[known | (unknown & include_unknown)]
res = res.sort_values("performance_score", ascending=False)

st.write(f"**{len(res)} players** match your filters")
view = res.assign(age_=res.age.round(0), value=res.market_value_eur.map(fmt_value),
                  rank=res.rank_in_position.astype("Int64").astype(str) + " / " + res.players_in_position.astype("Int64").astype(str))
st.dataframe(
    view[["player_name", "team", "league_name", "position_name", "age_", "value", "minutes",
          "performance_score", "rank"]].rename(columns={
        "player_name": "Player", "team": "Club", "league_name": "League", "position_name": "Position",
        "age_": "Age", "value": "Market value", "minutes": "Minutes", "performance_score": "Performance",
        "rank": "Rank in position"}),
    hide_index=True, height=480)

if not res.empty:
    choice = st.selectbox("Open a player's profile", res.label.tolist())
    if st.button("View profile →", type="primary"):
        st.session_state["player_season_id"] = int(res.loc[res.label == choice, "player_season_id"].iloc[0])
        st.switch_page("pages/2_Player_Profile.py")

footer()