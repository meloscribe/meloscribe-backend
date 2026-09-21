import os
import sqlite3
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

DB_PATH = Path(__file__).parent / "analytics.db"
SETTINGS_FILE = Path(__file__).parent / "settings.json"

def get_directories():
    keysight_dir = Path(r"C:\Dev\meloscribe\Keysight export")
    cakewalk_dir = Path(r"C:\Cakewalk Projects")
    if SETTINGS_FILE.exists():
        try:
            import json
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
                if s.get("keysight_dir"): keysight_dir = Path(s["keysight_dir"])
                if s.get("cakewalk_dir"): cakewalk_dir = Path(s["cakewalk_dir"])
        except Exception:
            pass
    return keysight_dir, cakewalk_dir

def find_midi_file(song_name: str, cakewalk_dir: Path) -> Optional[Path]:
    base_name = song_name[:-5].strip() if song_name.lower().endswith(" easy") else song_name
    candidates = [
        cakewalk_dir / song_name / f"{song_name}.mid",
        cakewalk_dir / base_name / f"{song_name}.mid",
        cakewalk_dir / base_name / f"{base_name}.mid",
        cakewalk_dir / f"{song_name}.mid",
        cakewalk_dir / f"{base_name}.mid"
    ]
    for p in candidates:
        if p.exists():
            return p
    return None

def scan_evergreen_candidates(cooldown_days: int = 60, top_percentile: float = 0.70, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Identifies catalog songs ripe for re-upload:
    1. Performance in top 30% on Facebook/TikTok.
    2. Cooldown >= 60 days since last publish date or recycled release.
    3. Raw Keysight video and MIDI must exist locally.
    """
    if not DB_PATH.exists():
        return []
        
    keysight_dir, cakewalk_dir = get_directories()
    now = datetime.datetime.now(datetime.timezone.utc)
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. Fetch all performance stats from videos table for FB & TikTok
        query = """
            SELECT 
                song_name,
                author,
                platform,
                MAX(views) as max_views,
                MAX(publish_date) as last_published
            FROM videos 
            WHERE platform IN ('facebook', 'tiktok') AND views IS NOT NULL
            GROUP BY song_name, platform
        """
        rows = cursor.execute(query).fetchall()
        
        if not rows:
            return []
            
        # Group metrics by song
        songs_data: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            s_name = r["song_name"].strip()
            if s_name not in songs_data:
                songs_data[s_name] = {
                    "song_name": s_name,
                    "author": r["author"] or "",
                    "fb_views": 0,
                    "tiktok_views": 0,
                    "max_views": 0,
                    "best_platform": "facebook",
                    "last_published": None
                }
            v = r["max_views"] or 0
            if r["platform"] == "facebook":
                songs_data[s_name]["fb_views"] = max(songs_data[s_name]["fb_views"], v)
            elif r["platform"] == "tiktok":
                songs_data[s_name]["tiktok_views"] = max(songs_data[s_name]["tiktok_views"], v)
                
            if v > songs_data[s_name]["max_views"]:
                songs_data[s_name]["max_views"] = v
                songs_data[s_name]["best_platform"] = r["platform"]
                
            pub = r["last_published"]
            if pub:
                try:
                    # Clean ISO format
                    pub_clean = pub.replace("+0000", "+00:00")
                    dt = datetime.datetime.fromisoformat(pub_clean)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    if songs_data[s_name]["last_published"] is None or dt > songs_data[s_name]["last_published"]:
                        songs_data[s_name]["last_published"] = dt
                except Exception:
                    pass

        # 2. Calculate views threshold (Top 30%)
        all_views = [d["max_views"] for d in songs_data.values() if d["max_views"] > 0]
        if not all_views:
            return []
        all_views.sort()
        threshold_idx = int(len(all_views) * top_percentile)
        view_threshold = all_views[min(threshold_idx, len(all_views) - 1)]

        # 3. Check recycling_history table for recent recycling uploads
        cursor.execute("CREATE TABLE IF NOT EXISTS recycling_history (id INTEGER PRIMARY KEY AUTOINCREMENT, song_name TEXT, start_time REAL, end_time REAL, segment_type TEXT, status TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        rec_rows = cursor.execute("SELECT song_name, MAX(created_at) FROM recycling_history WHERE status IN ('staged', 'published', 'rendered') GROUP BY song_name").fetchall()
        recent_recycles = {}
        for r_name, r_date in rec_rows:
            if r_name and r_date:
                try:
                    dt = datetime.datetime.fromisoformat(r_date)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    recent_recycles[r_name.strip().lower()] = dt
                except Exception:
                    pass

        candidates = []
        for s_name, data in songs_data.items():
            # Check performance threshold
            if data["max_views"] < view_threshold:
                continue
                
            # Check cooldown against both videos and recycling_history
            last_dt = data["last_published"]
            rec_dt = recent_recycles.get(s_name.strip().lower())
            most_recent_date = last_dt
            if rec_dt and (most_recent_date is None or rec_dt > most_recent_date):
                most_recent_date = rec_dt
                
            if most_recent_date:
                days_ago = (now - most_recent_date).days
                if days_ago < cooldown_days:
                    continue
            else:
                days_ago = 999  # Never published
                
            # Check local file availability
            raw_video = keysight_dir / f"{s_name}.mp4"
            if not raw_video.exists():
                raw_video = keysight_dir / f"{s_name} slow.mp4"
            if not raw_video.exists():
                continue
                
            midi_file = find_midi_file(s_name, cakewalk_dir)
            if not midi_file:
                continue

            # Format views label
            v_val = data["max_views"]
            v_str = f"{v_val // 1000}k" if v_val >= 1000 else str(v_val)
            platform_str = "FB" if data["best_platform"] == "facebook" else "TikTok"
            
            candidates.append({
                "song_name": s_name,
                "author": data["author"],
                "days_ago": days_ago,
                "days_label": f"Letzter Post vor {days_ago} Tagen" if days_ago < 900 else "Bereit für Erst-Recycling",
                "max_views": v_val,
                "performance_label": f"{v_str} {platform_str} Views",
                "video_path": str(raw_video),
                "midi_path": str(midi_file)
            })

        # Sort by highest views descending
        candidates.sort(key=lambda c: c["max_views"], reverse=True)
        return candidates[:limit]

if __name__ == "__main__":
    import json
    print("Scanning Evergreen Candidates...")
    top_cands = scan_evergreen_candidates()
    print(json.dumps(top_cands, indent=2))
