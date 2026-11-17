"""
Data Collection Module — StatsBomb Open Data downloader.

Downloads matches, events and lineups for the leagues listed in
config/settings.yaml and caches them as compressed JSON under
data/raw/statsbomb/. Files already on disk are skipped, so the
download can be stopped and re-run safely (it resumes).

Usage (from the project root, with .venv active):
    python -m scoutiq.collection.statsbomb premier_league
    python -m scoutiq.collection.statsbomb                 # all leagues
    python -m scoutiq.collection.statsbomb premier_league --summary
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from scoutiq.config import PROJECT_ROOT, load_settings

# Public GitHub location of StatsBomb's open data
BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"


# ---------- small helpers ----------

def _raw_root() -> Path:
    """Folder where all raw StatsBomb files are stored."""
    return PROJECT_ROOT / load_settings()["paths"]["raw"] / "statsbomb"


def _get_json(url: str, retries: int = 3):
    """Download a JSON file, retrying a few times on network errors."""
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == retries:
                raise
            time.sleep(2 * attempt)  # wait a bit longer after each failure


def _write_gz_json(data, path: Path) -> None:
    """Save data as gzip-compressed JSON. Writes to a temp file first,
    so an interrupted download never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(data, f)
    tmp.replace(path)


def read_gz_json(path: Path):
    """Load a file saved by _write_gz_json. Used by later modules too."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


# ---------- downloading ----------

def download_matches(competition_id: int, season_id: int, raw_root: Path) -> list[dict]:
    """Download (or load from cache) the list of matches for one league-season."""
    path = raw_root / "matches" / f"{competition_id}_{season_id}.json.gz"
    if path.exists():
        return read_gz_json(path)
    matches = _get_json(f"{BASE_URL}/matches/{competition_id}/{season_id}.json")
    _write_gz_json(matches, path)
    return matches


def _download_match_files(match_id: int, raw_root: Path) -> None:
    """Download the events and lineups files for one match, unless cached."""
    for kind in ("events", "lineups"):
        path = raw_root / kind / f"{match_id}.json.gz"
        if not path.exists():
            _write_gz_json(_get_json(f"{BASE_URL}/{kind}/{match_id}.json"), path)


def download_league(league: str, workers: int = 6) -> dict:
    """Download every match of one league. Returns a small report."""
    settings = load_settings()
    competition_id = settings["competitions"][league]
    season_id = settings["season_id"]
    raw_root = _raw_root()

    matches = download_matches(competition_id, season_id, raw_root)
    match_ids = [m["match_id"] for m in matches]
    print(f"{league}: {len(match_ids)} matches found")

    failed = []
    # Several downloads run in parallel to save time
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download_match_files, mid, raw_root): mid for mid in match_ids}
        for i, future in enumerate(as_completed(futures), start=1):
            try:
                future.result()
            except Exception as exc:  # report and keep going
                failed.append(futures[future])
                print(f"  ! match {futures[future]} failed: {exc}")
            if i % 25 == 0 or i == len(match_ids):
                print(f"  {i}/{len(match_ids)} matches processed")

    if failed:
        print(f"  {len(failed)} matches failed. Re-run the same command to retry only those.")
    else:
        print(f"  {league}: complete.")
    return {"league": league, "matches": len(match_ids), "failed": failed}


# ---------- inspection ----------

def summarise(league: str) -> None:
    """Print what has been downloaded for a league and what one match looks like."""
    settings = load_settings()
    raw_root = _raw_root()
    matches = download_matches(settings["competitions"][league], settings["season_id"], raw_root)
    ids = [m["match_id"] for m in matches]
    have_events = sum((raw_root / "events" / f"{i}.json.gz").exists() for i in ids)
    have_lineups = sum((raw_root / "lineups" / f"{i}.json.gz").exists() for i in ids)
    print(f"\n=== {league} ===")
    print(f"Matches listed: {len(ids)} | events files: {have_events} | lineups files: {have_lineups}")
    if not have_events:
        return

    sample_id = next(i for i in ids if (raw_root / "events" / f"{i}.json.gz").exists())
    events = read_gz_json(raw_root / "events" / f"{sample_id}.json.gz")
    print(f"\nSample match {sample_id}: {len(events)} events")
    print("Most common event types:")
    for name, count in Counter(e["type"]["name"] for e in events).most_common(12):
        print(f"  {name:<20} {count}")

    lineups_path = raw_root / "lineups" / f"{sample_id}.json.gz"
    if lineups_path.exists():
        lineups = read_gz_json(lineups_path)
        players = [p for team in lineups for p in team["lineup"]]
        with_dob = sum(bool(p.get("birth_date")) for p in players)
        with_positions = sum(bool(p.get("positions")) for p in players)
        print(f"\nLineups: {len(players)} players | birth date present: {with_dob} "
              f"| position history present: {with_positions}")


# ---------- command line ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="Download StatsBomb open data for ScoutIQ.")
    parser.add_argument("leagues", nargs="*",
                        help="League keys from settings.yaml (leave empty for all)")
    parser.add_argument("--workers", type=int, default=6, help="Parallel downloads")
    parser.add_argument("--summary", action="store_true",
                        help="Only summarise what is already downloaded")
    args = parser.parse_args()

    leagues = args.leagues or list(load_settings()["competitions"])
    for league in leagues:
        if args.summary:
            summarise(league)
        else:
            download_league(league, workers=args.workers)


if __name__ == "__main__":
    main()