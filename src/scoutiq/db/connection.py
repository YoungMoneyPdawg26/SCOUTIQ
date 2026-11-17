"""Creates the SQLAlchemy connection to MySQL using config/database.yaml."""
from __future__ import annotations

from functools import lru_cache

import yaml
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL

from scoutiq.config import PROJECT_ROOT


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    path = PROJECT_ROOT / "config" / "database.yaml"
    if not path.exists():
        raise FileNotFoundError("config/database.yaml not found. Copy config/database.example.yaml and add your password.")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    url = URL.create("mysql+pymysql", username=cfg["user"], password=str(cfg["password"]),
                     host=cfg["host"], port=cfg["port"], database=cfg["database"])
    return create_engine(url, pool_pre_ping=True)