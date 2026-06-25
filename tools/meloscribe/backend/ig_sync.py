"""
Instagram Analytics Sync
------------------------
Fetches all your Instagram Reels stats and writes them into analytics.db.
Runs automatically on app startup (called from main.py).
Can also be triggered manually: python ig_sync.py
"""
import os
import sys
import json
import sqlite3
import requests
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "analytics.db"
TOKENS_PATH = Path(__file__).parent / "ig_tokens.json"


def get_ig_credentials():
    if not TOKENS_PATH.exists():
        return None, None
    with open(TOKENS_PATH, "r") as f:
        data = json.load(f)
    return data.get("access_token"), data.get("ig_business_id")


def _get_all_media(access_token: str, ig_business_id: str) -> list[dict]:
    """Page through the media list and return id, caption, like_count, comments_count."""
    media = []
    url = f"https://graph.facebook.com/v19.0/{ig_business_id}/media"
    params = {
        "fields": "id,caption,media_product_type,like_count,comments_count,timestamp",
        "access_token": access_token,
        "limit": 50
    }

    while url:
        resp = requests.get(url, params=params if url.startswith("https://graph.facebook.com/v19.0/") else None)
        if resp.status_code != 200:
            print(f"  [Instagram] API error {resp.status_code}: {resp.text}")
            break

        body = resp.json()
        data = body.get("data", [])
        
        # Only keep REELS (if we only want Reels)
        # Media types: AD, FEED, STORY, REELS
        for item in data:
            if item.get("media_product_type") == "REELS":
                media.append(item)

        url = body.get("paging", {}).get("next")
        params = None # the 'next' URL already contains the params
        time.sleep(0.5)

    print(f"  [Instagram] Found {len(media)} Reels total.")
    return media


def _get_video_insights(access_token: str, media_ids: list[str]) -> dict:
    """Batch-query the 'views', 'shares', 'saved', and 'reach' insights."""
    insights_map = {}
    batch_size = 50

    for i in range(0, len(media_ids), batch_size):
        batch = media_ids[i:i + batch_size]
        batch_requests = []
        for mid in batch:
            batch_requests.append({
                "method": "GET",
                "relative_url": f"v19.0/{mid}/insights?metric=views,shares,saved,reach"
            })
            
        resp = requests.post(
            "https://graph.facebook.com",
            data={
                "access_token": access_token,
                "batch": json.dumps(batch_requests)
            }
        )
        
        if resp.status_code != 200:
            print(f"  [Instagram] Batch error {resp.status_code}: {resp.text}")
            continue

        body = resp.json()
        for idx, item in enumerate(body):
            mid = batch[idx]
            insights_map[mid] = {"views": 0, "shares": 0, "saved": 0, "reach": 0}
            
            if item.get("code") == 200:
                item_data = json.loads(item["body"])
                data_list = item_data.get("data", [])
                for metric in data_list:
                    metric_name = metric.get("name")
                    val = metric.get("values", [{}])[0].get("value", 0)
                    if metric_name in insights_map[mid]:
                        insights_map[mid][metric_name] = val
                        
        time.sleep(0.5)

    return insights_map

def _get_profile_insights(access_token: str, ig_business_id: str) -> dict:
    """Get profile views and website clicks for the last day."""
    url = f"https://graph.facebook.com/v19.0/{ig_business_id}/insights"
    params = {
        "metric": "profile_views,website_clicks",
        "period": "day",
        "access_token": access_token
    }
    try:
        resp = requests.get(url, params=params).json()
        res = {"profile_views": 0, "website_clicks": 0}
        for metric in resp.get("data", []):
            name = metric.get("name")
            vals = metric.get("values", [])
            if vals and name in res:
                res[name] = vals[-1].get("value", 0)
        return res
    except Exception as e:
        print(f"  [Instagram] Error fetching profile insights: {e}")
        return {"profile_views": 0, "website_clicks": 0}


def sync_instagram():
    print("[Instagram Sync] Starting...")

    token, ig_id = get_ig_credentials()
    if not token or not ig_id:
        print("[Instagram Sync] No valid token. Skipping sync.")
        print("[Instagram Sync] Run ig_setup.py to authorize first.")
        return False

    import sync_utils
    import datetime

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    known_songs = sync_utils.get_known_songs(cursor)

    # 1. Profile Insights
    prof_insights = _get_profile_insights(token, ig_id)
    today = datetime.datetime.utcnow().strftime('%Y-%m-%d')
    cursor.execute('''
        INSERT INTO channel_insights (platform, date, profile_views, website_clicks)
        VALUES ('instagram', ?, ?, ?)
        ON CONFLICT(platform, date) DO UPDATE SET
            profile_views = excluded.profile_views,
            website_clicks = excluded.website_clicks
    ''', (today, prof_insights['profile_views'], prof_insights['website_clicks']))

    # 2. Get all Reels
    print("[Instagram Sync] Fetching Reels data...")
    media_list = _get_all_media(token, ig_id)
    if not media_list:
        print("[Instagram Sync] No Reels found on account.")
        conn.close()
        return False

    media_ids = [m["id"] for m in media_list]

    # 3. Get view insights for all Reels
    print("[Instagram Sync] Fetching detailed insights in batches...")
    insights_map = _get_video_insights(token, media_ids)

    # 4. Write to analytics.db
    synced = 0
    skipped = 0
    for m in media_list:
        mid       = m.get("id")
        caption   = m.get("caption", "")
        likes     = m.get("like_count", 0)
        comments  = m.get("comments_count", 0)
        publish_date = m.get("timestamp", "")
        url       = m.get("permalink", "")
        
        insight_data = insights_map.get(mid, {"views": 0, "shares": 0, "saved": 0, "reach": 0})
        views     = insight_data.get("views", 0)
        shares    = insight_data.get("shares", 0)
        saved     = insight_data.get("saved", 0)
        reach     = insight_data.get("reach", 0)

        # Match to song
        song_name = sync_utils.match_song_name(caption, known_songs, cursor)
        if not song_name:
            skipped += 1
            continue

        author = sync_utils.extract_author(song_name, caption)
        language = sync_utils.detect_language(song_name)
        format_type = sync_utils.detect_format(caption)

        video_data = {
            "id": f"ig_{mid}",
            "song_name": song_name,
            "platform": "instagram",
            "title": caption[:100],
            "author": author,
            "language": language,
            "publish_date": publish_date,
            "duration_sec": 0, # not provided easily
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "saves": saved,
            "reach": reach,
            "watch_time_min": 0,
            "avg_view_pct": 0,
            "ctr": 0,
            "format": format_type,
            "url": url
        }

        sync_utils.upsert_video(cursor, video_data)
        print(f"  [ok] {song_name}: {views:,} views | {saved} saves")
        synced += 1

    conn.commit()
    conn.close()

    print(f"[Instagram Sync] Done! {synced} videos synced, {skipped} skipped.")
    return True


if __name__ == "__main__":
    sync_instagram()
