"""
Inventory snapshot / backup helper.

One public coroutine: snapshot_inventory(reason)

Called at the start of every mutating async wrapper in excel_db.py (before the
lock is acquired) and once per background price-update cycle.  Errors are
swallowed and logged to stdout so a backup failure never blocks a mutation.
"""

import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import config


async def snapshot_inventory(reason: str) -> Optional[Path]:
    """
    Copy inventory.xlsx → backups/inventory-YYYYMMDD-HHMMSS-<reason>.xlsx.

    Returns the snapshot path, or None if the source file doesn't exist yet
    (e.g. first boot before init_excel has run) or the copy fails.

    After writing, applies retention: keeps only the most recent
    config.BACKUP_RETENTION_COUNT snapshots and deletes older ones.
    """
    src = Path(config.EXCEL_FILE)
    if not src.exists():
        return None

    backup_dir = Path(config.BACKUP_DIR)

    try:
        await asyncio.to_thread(backup_dir.mkdir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"inventory-{timestamp}-{reason}.xlsx"
        await asyncio.to_thread(shutil.copy2, str(src), str(dest))
    except Exception as exc:
        print(f"[backup] Warning: snapshot failed ({exc})")
        return None

    # Retention — trim to the N most-recent snapshots
    try:
        snapshots = sorted(
            backup_dir.glob("inventory-*.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in snapshots[config.BACKUP_RETENTION_COUNT:]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[backup] Warning: retention cleanup failed ({exc})")

    return dest
