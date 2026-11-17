-- ScoutIQ MySQL schema. Re-running this file rebuilds the database from scratch.
DROP VIEW IF EXISTS v_player_overview;
DROP TABLE IF EXISTS shortlists;
DROP TABLE IF EXISTS club_requirements;
DROP TABLE IF EXISTS score_components;
DROP TABLE IF EXISTS performance_scores;
DROP TABLE IF EXISTS market_values;
DROP TABLE IF EXISTS player_stats;
DROP TABLE IF EXISTS metrics;
DROP TABLE IF EXISTS player_seasons;
DROP TABLE IF EXISTS players;
DROP TABLE IF EXISTS clubs;
DROP TABLE IF EXISTS leagues;

CREATE TABLE leagues (
    league_id               VARCHAR(30) PRIMARY KEY,
    name                    VARCHAR(60) NOT NULL,
    country                 VARCHAR(40) NOT NULL,
    statsbomb_competition_id INT NOT NULL,
    in_scoring_pool         BOOLEAN NOT NULL
);

CREATE TABLE clubs (
    club_id    INT AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(80) NOT NULL UNIQUE,
    league_id  VARCHAR(30) NOT NULL,
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
);

CREATE TABLE players (
    player_id      INT PRIMARY KEY,              -- StatsBomb player id
    name           VARCHAR(120) NOT NULL,
    nickname       VARCHAR(80),
    nationality    VARCHAR(60),
    date_of_birth  DATE,                         -- from Transfermarkt (NULL if unmatched)
    tm_player_id   INT,                          -- Transfermarkt id (NULL if unmatched)
    match_status   VARCHAR(20) NOT NULL          -- matched / unmatched / review
);

CREATE TABLE player_seasons (
    player_season_id INT AUTO_INCREMENT PRIMARY KEY,
    player_id        INT NOT NULL,
    league_id        VARCHAR(30) NOT NULL,
    club_id          INT NOT NULL,
    season           VARCHAR(9) NOT NULL,
    position_group   CHAR(2),
    minutes          DECIMAL(7,1) NOT NULL,
    matches          INT NOT NULL,
    age              DECIMAL(4,1),               -- age on 1 Jan 2016
    UNIQUE (player_id, league_id, season),
    FOREIGN KEY (player_id) REFERENCES players(player_id),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id),
    FOREIGN KEY (club_id)   REFERENCES clubs(club_id)
);

CREATE TABLE metrics (
    metric_code      VARCHAR(40) PRIMARY KEY,
    label            VARCHAR(80) NOT NULL,
    category         VARCHAR(30) NOT NULL,
    higher_is_better BOOLEAN NOT NULL,
    kind             ENUM('count','ratio') NOT NULL,
    data_status      ENUM('actual','derived','estimated','unavailable') NOT NULL
);

CREATE TABLE player_stats (
    player_season_id INT NOT NULL,
    metric_code      VARCHAR(40) NOT NULL,
    total            DECIMAL(12,4),              -- season total (count metrics)
    per90            DECIMAL(12,4),              -- per 90 minutes (count metrics)
    ratio_value      DECIMAL(6,2),               -- percentage (ratio metrics)
    PRIMARY KEY (player_season_id, metric_code),
    FOREIGN KEY (player_season_id) REFERENCES player_seasons(player_season_id),
    FOREIGN KEY (metric_code) REFERENCES metrics(metric_code)
);

CREATE TABLE market_values (
    player_id      INT NOT NULL,
    valuation_date DATE NOT NULL,
    value_eur      BIGINT NOT NULL,
    source         VARCHAR(40) NOT NULL DEFAULT 'Transfermarkt (Kaggle CC0)',
    data_status    VARCHAR(20) NOT NULL DEFAULT 'estimated',
    PRIMARY KEY (player_id, valuation_date),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);

CREATE TABLE performance_scores (
    player_season_id    INT NOT NULL,
    model_version       VARCHAR(10) NOT NULL,
    performance_score   DECIMAL(5,1) NOT NULL,
    rank_in_position    INT NOT NULL,
    players_in_position INT NOT NULL,
    PRIMARY KEY (player_season_id, model_version),
    FOREIGN KEY (player_season_id) REFERENCES player_seasons(player_season_id)
);

CREATE TABLE score_components (
    player_season_id INT NOT NULL,
    model_version    VARCHAR(10) NOT NULL,
    component_type   ENUM('category','metric') NOT NULL,
    component_code   VARCHAR(40) NOT NULL,
    score            DECIMAL(5,1),               -- category score or metric percentile (0-100)
    PRIMARY KEY (player_season_id, model_version, component_type, component_code),
    FOREIGN KEY (player_season_id) REFERENCES player_seasons(player_season_id)
);

CREATE TABLE club_requirements (
    requirement_id    INT AUTO_INCREMENT PRIMARY KEY,
    club_name         VARCHAR(80) NOT NULL,
    position_group    CHAR(2) NOT NULL,
    style             VARCHAR(40) NOT NULL,
    age_min           INT NOT NULL,
    age_max           INT NOT NULL,
    ideal_age_min     INT,
    ideal_age_max     INT,
    budget_max_eur    BIGINT NOT NULL,
    attribute_targets JSON,
    importance        JSON,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE shortlists (
    requirement_id   INT NOT NULL,
    player_season_id INT NOT NULL,
    shortlist_rank   INT NOT NULL,
    fit_score        DECIMAL(5,1) NOT NULL,
    fit_style        DECIMAL(5,1),
    fit_attributes   DECIMAL(5,1),
    fit_age          DECIMAL(5,1),
    fit_value        DECIMAL(5,1),
    explanation      TEXT,
    status           VARCHAR(20) NOT NULL DEFAULT 'proposed',
    notes            TEXT,
    PRIMARY KEY (requirement_id, player_season_id),
    FOREIGN KEY (requirement_id) REFERENCES club_requirements(requirement_id) ON DELETE CASCADE,
    FOREIGN KEY (player_season_id) REFERENCES player_seasons(player_season_id)
);

-- One flat row per scored player: used by the Streamlit app and dashboards
CREATE VIEW v_player_overview AS
SELECT ps.player_season_id, p.player_id, p.name AS player_name, p.nickname, p.nationality,
       c.name AS club, l.name AS league, ps.position_group, ps.minutes, ps.matches, ps.age,
       mv.value_eur AS market_value_eur, sc.performance_score, sc.rank_in_position,
       sc.players_in_position, sc.model_version
FROM player_seasons ps
JOIN players p   ON p.player_id = ps.player_id
JOIN clubs c     ON c.club_id = ps.club_id
JOIN leagues l   ON l.league_id = ps.league_id
LEFT JOIN performance_scores sc ON sc.player_season_id = ps.player_season_id
LEFT JOIN market_values mv ON mv.player_id = p.player_id;