"""Loads project settings from config/settings.yaml."""
from pathlib import Path

import yaml

# src/scoutiq/config.py -> parents[2] is the project root (the SCOUTIQ folder)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_settings() -> dict:
    """Read config/settings.yaml and return it as a dictionary."""
    with open(PROJECT_ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)