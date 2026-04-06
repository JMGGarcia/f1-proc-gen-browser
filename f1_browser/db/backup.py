"""
DB backup and restore utilities.

Backups are stored in f1_browser/backups/ as:
    f1_world_{world_id8}_season_{season_number:04d}.db

Retention policy (applied after each backup):
  - Keep all backups where season_number % 100 == 0
  - Keep the 2 most recent season backups for the current world
  - Delete everything else for the current world
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

DB_PATH = Path("f1_world.db")
BACKUP_DIR = Path("backups")

_BACKUP_RE = re.compile(r"^f1_world_([0-9a-f]{8})_season_(\d{4})\.db$")


def get_world_id8(db) -> str:
    from db import models as m
    meta = db.query(m.WorldMeta).first()
    if meta is None:
        raise RuntimeError("WorldMeta not found — has the world been seeded?")
    return meta.world_id[:8]


def make_backup(
    season_number: int,
    db,
    db_path: Path = DB_PATH,
    backup_dir: Path = BACKUP_DIR,
) -> Path:
    world_id8 = get_world_id8(db)
    backup_dir.mkdir(exist_ok=True)
    dest = backup_dir / f"f1_world_{world_id8}_season_{season_number:04d}.db"
    shutil.copy2(db_path, dest)
    return dest


def cleanup_backups(world_id8: str, backup_dir: Path = BACKUP_DIR) -> None:
    if not backup_dir.exists():
        return

    entries: list[tuple[int, Path]] = []
    for p in backup_dir.iterdir():
        m = _BACKUP_RE.match(p.name)
        if m and m.group(1) == world_id8:
            entries.append((int(m.group(2)), p))

    entries.sort(key=lambda x: x[0])

    # Determine which season numbers to keep
    milestone_seasons = {n for n, _ in entries if n % 100 == 0}
    recent_seasons = {n for n, _ in entries[-2:]}
    keep = milestone_seasons | recent_seasons

    for season_num, path in entries:
        if season_num not in keep:
            path.unlink()


def list_backups(backup_dir: Path = BACKUP_DIR) -> list[tuple[str, int, Path]]:
    """Return (world_id8, season_number, path) sorted by world_id8 then season."""
    if not backup_dir.exists():
        return []

    results: list[tuple[str, int, Path]] = []
    for p in backup_dir.iterdir():
        m = _BACKUP_RE.match(p.name)
        if m:
            results.append((m.group(1), int(m.group(2)), p))

    results.sort(key=lambda x: (x[0], x[1]))
    return results
