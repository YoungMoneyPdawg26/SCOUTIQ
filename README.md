# ScoutIQ ⚽
**A data-driven football player evaluation and recruitment decision-support platform.**

ScoutIQ evaluates players from Europe's top leagues with position-specific statistical models.
It produces two independent scores:
- **Performance Score (0–100):** how well a player performs compared with others in the same position.
- **Recruitment-Fit Score (0–100):** how well a player suits a specific club's brief (style, attributes, age, budget).

It also generates ranked, explained shortlists.

> Example: for a cheap (≤ €15m), young, ball-playing centre-back in 2015/16, ScoutIQ's #1 pick is
> **Virgil van Dijk** (Southampton, €12m), who later joined Liverpool for about £75m.

## Features
- Recruitment dashboard (KPIs, position distribution, performance vs market value)
- Player search with filters (position, league, age, value, score, club)
- Player profiles: score breakdown, strengths and weaknesses, comparison with positional peers
- Player comparison with radar charts
- Club recruitment brief → ranked shortlist with plain-English explanations, saved to MySQL

## Tech stack
Python · Pandas · NumPy · scikit-learn · MySQL · SQLAlchemy · Streamlit · Plotly

## Data sources
| Data | Source | Notes |
|---|---|---|
| Match events 2015/16 (PL, La Liga, Serie A, Ligue 1) | [StatsBomb Open Data](https://github.com/statsbomb/open-data) | Used under StatsBomb's open-data terms; not re-hosted here |
| Market values and dates of birth | [Transfermarkt dataset on Kaggle](https://www.kaggle.com/datasets/davidcariboo/player-scores) | CC0; values are Transfermarkt estimates |

The Bundesliga is excluded from scoring because the open data only contains Bayer Leverkusen's matches for 2015/16.

## Methodology (summary)
1. Per-90 statistics from ~5.6 million events; players need 900+ minutes to be scored.
2. Each metric becomes a percentile within the player's position group (reversed for "lower is better" metrics).
3. Category scores are averages of metric percentiles; the Performance Score is a weighted average of
   categories, with weights per position (`config/scoring.yaml`).
4. The Recruitment-Fit Score applies hard filters (position, age, budget), then weights style match,
   attribute targets, age fit and value fit using the club's own importance settings.

Every metric is labelled **actual** (counted directly) or **derived** (calculated with a documented rule).

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp config/database.example.yaml config/database.yaml   # then add your MySQL password
mysql -u root -p -e "CREATE DATABASE scoutiq;"
```
Download `players.csv` and `player_valuations.csv` from the Kaggle dataset into `data/raw/transfermarkt/`.

## Build the data and run
```bash
python -m scoutiq.collection.statsbomb          # download StatsBomb data (~20-30 min)
python -m scoutiq.features.player_stats         # per-90 player statistics
python -m scoutiq.cleaning.player_matching      # join ages and market values
python -m scoutiq.scoring.performance           # Performance Scores
python -m scoutiq.ranking.shortlist             # example shortlist
python -m scoutiq.db.loader                     # load everything into MySQL
streamlit run app/Home.py                       # launch the app
```

## Project structure
```
config/            settings, scoring weights, style profiles, example brief
src/scoutiq/
  collection/      data download
  cleaning/        minutes, positions, Transfermarkt matching
  features/        per-90 metrics
  scoring/         Performance Score, Recruitment-Fit Score
  ranking/         shortlisting
  db/              MySQL schema, loader, queries
app/               Streamlit app (dashboard + 4 pages)
```

## Limitations
Event data captures on-ball actions only. Team style and league strength affect raw numbers.
Market values are crowd-sourced estimates. Weights are a documented, judgement-based starting point (model v0).
ScoutIQ supports scouting decisions; it does not replace scouting.

## Author
Pranit · Final-year B.Tech (Computer Science and Design) project