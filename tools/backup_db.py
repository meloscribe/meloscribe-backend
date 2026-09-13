#!/usr/bin/env python3
"""
Automated Database and Configuration Backup for meloscribe.
Safely copies SQLite databases using the sqlite3 online backup API,
archives configuration and tokens into a timestamped zip,
and enforces a 30-day retention policy.
"""

import os
import sys
import time
import zipfile
import sqlite3
import datetime
from pathlib import Path

def backup_sqlite_db(source_path: Path, target_path: Path) -> bool:
    """Use sqlite3 online backup API to create a consistent snapshot without locks."""
    if not source_path.exists():
        return False
    try:
        source_conn = sqlite3.connect(str(source_path), timeout=30.0)
        target_conn = sqlite3.connect(str(target_path))
        with target_conn:
            source_conn.backup(target_conn, pages=100, sleep=0.01)
        target_conn.close()
        source_conn.close()
        return True
    except Exception as e:
        print(f"[Backup] Warning: SQLite online backup failed for {source_path.name}: {e}")
        return False

def run_backup(base_dir: Path = None, backups_dir: Path = None, retention_days: int = 30):
    now = datetime.datetime.now(datetime.timezone.utc)
    date_str = now.strftime("%Y_%m_%d")
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    
    if base_dir is None:
        if os.name == "nt":
            base_dir = Path("C:/Dev/meloscribe-app/tools/meloscribe/backend")
        else:
            base_dir = Path("/home/ubuntu/meloscribe/tools/meloscribe/backend")
            
    if backups_dir is None:
        if os.name == "nt":
            backups_dir = Path("C:/Dev/meloscribe-app/backups")
        else:
            backups_dir = Path("/home/ubuntu/meloscribe/backups")
            
    backups_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = backups_dir / "temp_snapshot"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    zip_filename = f"meloscribe_backup_{date_str}_{now.strftime('%H%M%S')}.zip"
    zip_path = backups_dir / zip_filename
    
    print(f"[Backup] [{now.isoformat()}] Starting backup to: {zip_path}")
    
    # 1. Back up SQLite databases safely
    db_candidates = [
        base_dir / "analytics.db",
        base_dir.parent.parent / "queue.db",
        base_dir / "queue.db",
    ]
    
    files_to_zip = []
    
    for db in db_candidates:
        if db.exists():
            snap_path = temp_dir / f"{db.stem}_snapshot.db"
            if backup_sqlite_db(db, snap_path):
                files_to_zip.append((snap_path, db.name))
                print(f"[Backup] Successfully snapshotted {db.name} ({snap_path.stat().st_size:,} bytes)")
    
    # 2. Add config and credentials
    config_candidates = [
        (base_dir / "settings.json", "settings.json"),
        (base_dir / "api_key.txt", "api_key.txt"),
        (base_dir / "pinterest_tokens.json", "pinterest_tokens.json"),
        (base_dir / "tiktok_tokens.json", "tiktok_tokens.json"),
        (base_dir / "ig_tokens.json", "ig_tokens.json"),
        (base_dir / "threads_tokens.json", "threads_tokens.json"),
        (base_dir / "yt_tokens.json", "yt_tokens.json"),
        (base_dir / "songs.json", "songs.json"),
    ]
    
    for cfg_path, arcname in config_candidates:
        if cfg_path.exists():
            files_to_zip.append((cfg_path, arcname))
            
    # 3. Create ZIP archive
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
            for fpath, arcname in files_to_zip:
                zipf.write(fpath, arcname)
        print(f"[Backup] Archive created successfully ({zip_path.stat().st_size:,} bytes, {len(files_to_zip)} files)")
    except Exception as e:
        print(f"[Backup] ERROR creating zip archive: {e}")
        return False
    finally:
        # Clean up temporary snapshots
        for f in temp_dir.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass
        try:
            temp_dir.rmdir()
        except Exception:
            pass
            
    # 4. Enforce Retention Policy (Purge backups older than retention_days)
    try:
        cutoff_time = time.time() - (retention_days * 86400)
        purged = 0
        for bfile in backups_dir.glob("meloscribe_backup_*.zip"):
            if bfile.stat().st_mtime < cutoff_time:
                bfile.unlink()
                purged += 1
        if purged > 0:
            print(f"[Backup] Purged {purged} backup(s) older than {retention_days} days.")
    except Exception as e:
        print(f"[Backup] Warning during retention cleanup: {e}")
        
    return True

if __name__ == "__main__":
    success = run_backup()
    sys.exit(0 if success else 1)
