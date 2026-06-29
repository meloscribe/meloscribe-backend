"""
TikTok Analytics Sync
----------------------
Fetches all your TikTok video stats and writes them into analytics.db.
Runs automatically on app startup (called from main.py).
Can also be triggered manually: python tiktok_sync.py
"""
import os
import sys
import sqlite3
import requests
import time
from pathlib import Path

# Add backend dir to path so we can import tiktok_auth
sys.path.insert(0, str(Path(__file__).parent))
from tiktok_auth import get_valid_token

DB_PATH = Path(__file__).parent / "analytics.db"

VIDEO_LIST_URL  = "https://open.tiktokapis.com/v2/video/list/"
VIDEO_QUERY_URL = "https://open.tiktokapis.com/v2/video/query/"

VIDEO_FIELDS = "id,title,create_time,share_url,view_count,like_count,comment_count,share_count,duration"

def _get_tiktok_followers(access_token: str) -> int:
    """Fetch user follower_count from TikTok open API."""
    url = "https://open.tiktokapis.com/v2/user/info/"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"fields": "follower_count"}
    try:
        resp = requests.get(url, headers=headers, params=params).json()
        data = resp.get("data", {})
        user = data.get("user", {})
        return user.get("follower_count", 0)
    except Exception as e:
        print(f"  [TikTok] Error fetching user info: {e}")
        return 0



def _get_all_video_ids(access_token: str) -> list[dict]:
    """Page through the video list and return id + title for all videos."""
    videos = []
    cursor = 0
    page = 1

    while True:
        print(f"  [TikTok] Fetching page {page} of video list...")
        resp = requests.post(
            VIDEO_LIST_URL,
            params={"fields": "id,title,create_time"},
            json={"max_count": 20, "cursor": cursor},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            print(f"  [TikTok] List error {resp.status_code}: {resp.text}")
            break

        body = resp.json()
        data = body.get("data", {})
        videos.extend(data.get("videos", []))

        if not data.get("has_more", False):
            break
        cursor = data.get("cursor", 0)
        page += 1
        time.sleep(0.5)

    print(f"  [TikTok] Found {len(videos)} videos total.")
    return videos


def _get_video_stats(access_token: str, video_ids: list[str]) -> list[dict]:
    """Batch-query full stats for a list of video IDs."""
    results = []
    batch_size = 20

    for i in range(0, len(video_ids), batch_size):
        batch = video_ids[i:i + batch_size]
        resp = requests.post(
            VIDEO_QUERY_URL,
            params={"fields": VIDEO_FIELDS},
            json={"filters": {"video_ids": batch}},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            print(f"  [TikTok] Query error {resp.status_code}: {resp.text}")
            continue

        body = resp.json()
        results.extend(body.get("data", {}).get("videos", []))
        time.sleep(0.3)

    return results


def _match_song_name(title: str, known_songs: list[str]) -> str | None:
    """
    Try to match a TikTok video title to a known song name from the DB.
    TikTok description always contains the song name (as per pipeline convention).
    We do a case-insensitive substring match.
    """
    title_lower = title.lower()
    for song in known_songs:
        if song.lower() in title_lower:
            return song
    return None


def sync_tiktok():
    print("[TikTok Sync] Starting...")

    token = get_valid_token()
    if not token:
        print("[TikTok Sync] No valid token. Skipping sync.")
        print("[TikTok Sync] Run: python tiktok_auth.py  to authorize first.")
        return False

    import sync_utils
    
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    known_songs = sync_utils.get_known_songs(cursor)
    print(f"[TikTok Sync] {len(known_songs)} known songs in DB.")

    # Fetch and save followers
    try:
        followers = _get_tiktok_followers(token)
        import datetime
        today_str = datetime.date.today().isoformat()
        cursor.execute('''
            INSERT INTO channel_insights (platform, date, followers)
            VALUES ('tiktok', ?, ?)
            ON CONFLICT(platform, date) DO UPDATE SET
                followers = excluded.followers
        ''', (today_str, followers))
        print(f"[TikTok Sync] Saved channel followers: {followers}")
    except Exception as fold_err:
        print(f"[TikTok Sync] Note: Could not save channel followers: {fold_err}")

    # 1. Get all video IDs
    videos_meta = _get_all_video_ids(token)
    if not videos_meta:
        print("[TikTok Sync] No videos found on account.")
        conn.close()
        return False

    video_ids = [v["id"] for v in videos_meta]

    # 2. Get full stats per video
    print("[TikTok Sync] Fetching detailed stats...")
    stats = _get_video_stats(token, video_ids)

    # 3. Write to analytics.db
    synced = 0
    skipped = 0
    for video in stats:
        vid_id    = video.get("id")
        title     = video.get("title", "")
        views     = video.get("view_count", 0)
        likes     = video.get("like_count", 0)
        comments  = video.get("comment_count", 0)
        shares    = video.get("share_count", 0)
        create_ts = video.get("create_time", 0)
        duration  = video.get("duration", 0)
        url       = video.get("share_url", "")

        # Match to song
        song_name = sync_utils.match_song_name(title, known_songs, cursor)
        if not song_name:
            skipped += 1
            continue
            
        author = sync_utils.extract_author(title)
        language = sync_utils.detect_language(title)
        format_type = sync_utils.detect_format(title)
        
        publish_date = ""
        if create_ts:
            import datetime
            publish_date = datetime.datetime.utcfromtimestamp(create_ts).isoformat()

        video_data = {
            "id": f"tt_{vid_id}",
            "song_name": song_name,
            "platform": "tiktok",
            "title": title,
            "author": author,
            "language": language,
            "publish_date": publish_date,
            "duration_sec": duration,
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "saves": 0, # Not available in TikTok basic API
            "reach": views, # Approximation
            "watch_time_min": 0,
            "avg_view_pct": 0,
            "ctr": 0,
            "format": format_type,
            "url": url
        }
        
        sync_utils.upsert_video(cursor, video_data)
        print(f"  [ok] {song_name}: {views:,} views | {likes:,} likes")
        synced += 1

    conn.commit()
    conn.close()

    print(f"[TikTok Sync] Done! {synced} videos synced, {skipped} skipped.")
    return True


if __name__ == "__main__":
    sync_tiktok()
