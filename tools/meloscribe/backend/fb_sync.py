"""
Facebook Page Analytics Sync
----------------------------
Fetches all your Facebook Page videos stats and writes them into analytics.db.
Uses the same access token as Instagram Graph API, since they are linked.
"""
import os
import sys
import json
import sqlite3
import requests
import time
from pathlib import Path

# Try importing ig_sync's token function, or reimplement it
sys.path.insert(0, str(Path(__file__).parent))
TOKENS_PATH = Path(__file__).parent / "ig_tokens.json"

try:
    from ig_sync import get_ig_credentials
except ImportError:

    def get_ig_credentials():
        if not TOKENS_PATH.exists():
            return None, None
        with open(TOKENS_PATH, "r") as f:
            data = json.load(f)
        return data.get("access_token"), data.get("ig_business_id")

DB_PATH = Path(__file__).parent / "analytics.db"

def _get_fb_page_id() -> str:
    """Gets the Facebook Page ID from the tokens file."""
    if not TOKENS_PATH.exists():
        return None
    with open(TOKENS_PATH, "r") as f:
        data = json.load(f)
    return data.get("fb_page_id")

def _get_fb_followers_count(access_token: str, page_id: str) -> int:
    """Fetch user followers_count from Facebook Page Graph API."""
    url = f"https://graph.facebook.com/v19.0/{page_id}"
    params = {
        "fields": "followers_count",
        "access_token": access_token
    }
    try:
        resp = requests.get(url, params=params).json()
        return resp.get("followers_count", 0)
    except Exception as e:
        print(f"  [Facebook] Error fetching followers count: {e}")
        return 0

def _get_all_fb_videos(access_token: str, page_id: str) -> list[dict]:
    """Page through the video list and return basic stats."""
    videos = []
    url = f"https://graph.facebook.com/v19.0/{page_id}/videos"
    params = {
        "fields": "id,title,description,created_time,length,permalink_url,views",
        "access_token": access_token,
        "limit": 50
    }

    while url:
        resp = requests.get(url, params=params if url.startswith("https://graph.facebook.com/v19.0/") else None)
        if resp.status_code != 200:
            print(f"  [Facebook] API error {resp.status_code}: {resp.text}")
            break

        body = resp.json()
        videos.extend(body.get("data", []))

        url = body.get("paging", {}).get("next")
        params = None
        time.sleep(0.5)

    print(f"  [Facebook] Found {len(videos)} videos total.")
    return videos

def sync_facebook():
    print("[Facebook Sync] Starting...")

    token, _ = get_ig_credentials()
    if not token:
        print("[Facebook Sync] No valid token. Skipping sync.")
        return False

    page_id = _get_fb_page_id()
    if not page_id:
        print("[Facebook Sync] Could not find associated Facebook Page.")
        return False

    import sync_utils
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    known_songs = sync_utils.get_known_songs(cursor)

    # Fetch and save followers
    try:
        followers = _get_fb_followers_count(token, page_id)
        import datetime
        today_str = datetime.date.today().isoformat()
        cursor.execute('''
            INSERT INTO channel_insights (platform, date, followers)
            VALUES ('facebook', ?, ?)
            ON CONFLICT(platform, date) DO UPDATE SET
                followers = excluded.followers
        ''', (today_str, followers))
        print(f"[Facebook Sync] Saved channel followers: {followers}")
    except Exception as fold_err:
        print(f"[Facebook Sync] Note: Could not save channel followers: {fold_err}")

    print("[Facebook Sync] Fetching Page videos...")
    videos_list = _get_all_fb_videos(token, page_id)
    if not videos_list:
        print("[Facebook Sync] No videos found.")
        conn.close()
        return False

    synced = 0
    skipped = 0
    for v in videos_list:
        vid       = v.get("id")
        title     = v.get("title", "")
        desc      = v.get("description", "")
        published = v.get("created_time", "")
        duration  = v.get("length", 0)
        url       = v.get("permalink_url", "")
        views     = v.get("views", 0)
        reach     = views  # Approximation since insights are broken

        # Match to song
        song_name = sync_utils.match_song_name(title or desc, known_songs, cursor)
        if not song_name:
            skipped += 1
            continue

        author = sync_utils.extract_author(title, desc)
        language = sync_utils.detect_language(title or desc)
        format_type = sync_utils.detect_format(title, desc)

        video_data = {
            "id": f"fb_{vid}",
            "song_name": song_name,
            "platform": "facebook",
            "title": (title or desc)[:100],
            "author": author,
            "language": language,
            "publish_date": published,
            "duration_sec": duration,
            "views": views,
            "likes": 0, # requires comments.summary(true),likes.summary(true) on the original request
            "comments": 0,
            "shares": 0,
            "saves": 0,
            "reach": reach,
            "watch_time_min": 0,
            "avg_view_pct": 0,
            "ctr": 0,
            "format": format_type,
            "url": url
        }

        sync_utils.upsert_video(cursor, video_data)
        print(f"  [ok] {song_name}: {views:,} views | {reach:,} reach")
        synced += 1

    conn.commit()
    conn.close()

    print(f"[Facebook Sync] Done! {synced} videos synced, {skipped} skipped.")
    return True

if __name__ == "__main__":
    sync_facebook()
