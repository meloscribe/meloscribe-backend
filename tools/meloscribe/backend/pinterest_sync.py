import json
import sqlite3
import datetime
import requests
from pathlib import Path

def sync_pinterest():
    print("[Pinterest Sync] Starting...")
    tokens_path = Path(__file__).parent / "pinterest_tokens.json"
    if not tokens_path.exists():
        print("[Pinterest Sync] No Pinterest credentials found. Skipping.")
        return
        
    try:
        with open(tokens_path, "r") as f:
            tokens = json.load(f)
    except Exception as e:
        print(f"[Pinterest Sync] Failed to read tokens: {e}")
        return

    access_token = tokens.get("pinterest_access_token")
    if not access_token:
        print("[Pinterest Sync] No access token found in tokens file. Skipping.")
        return

    followers = 0
    views = 0
    
    # 1. Fetch user account profile
    try:
        url = "https://api.pinterest.com/v5/user_account"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10
        )
        if resp.status_code == 403 and "use API Sandbox" in resp.text:
            print("[Pinterest Sync] App is in Trial mode. Retrying with Pinterest Sandbox API...")
            url = "https://api-sandbox.pinterest.com/v5/user_account"
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10
            )

        if resp.status_code == 200:
            data = resp.json()
            followers = int(data.get("follower_count", 0))
            views = int(data.get("monthly_views", 0))
            print(f"[Pinterest Sync] Fetched from user_account: followers={followers}, monthly_views={views}")
        else:
            print(f"[Pinterest Sync] API returned status code {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Pinterest Sync] Failed to fetch profile: {e}")

    # Connect to database
    db_path = Path(__file__).parent / "analytics.db"
    conn = sqlite3.connect(db_path, timeout=30.0)
    c = conn.cursor()
    
    today_str = datetime.date.today().isoformat()
    
    # 2. Save follower count to channel_insights
    try:
        c.execute("CREATE TABLE IF NOT EXISTS channel_insights (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, date TEXT, followers INTEGER, profile_views INTEGER, website_clicks INTEGER)")
        c.execute("DELETE FROM channel_insights WHERE platform = ? AND date = ?", ("pinterest", today_str))
        c.execute("INSERT INTO channel_insights (platform, date, followers, profile_views) VALUES (?, ?, ?, ?)", ("pinterest", today_str, followers, views))
        print(f"[Pinterest Sync] Saved channel followers={followers}, views={views}")
    except Exception as e:
        print(f"[Pinterest Sync] Failed to save channel insights: {e}")
        
    # 3. Save snapshot for the timeline
    try:
        c.execute("CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_date TEXT, song_name TEXT, platform TEXT, views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER)")
        c.execute("DELETE FROM snapshots WHERE platform = ? AND snapshot_date = ? AND song_name = ?", ("pinterest", today_str, ""))
        c.execute("INSERT INTO snapshots (snapshot_date, song_name, platform, views) VALUES (?, ?, ?, ?)", (today_str, "", "pinterest", views))
        print(f"[Pinterest Sync] Saved snapshot views: {views}")
    except Exception as e:
        print(f"[Pinterest Sync] Failed to save snapshot: {e}")

    conn.commit()
    conn.close()
    print("[Pinterest Sync] Done!")

if __name__ == "__main__":
    sync_pinterest()
