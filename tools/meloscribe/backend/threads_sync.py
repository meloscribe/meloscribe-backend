import json
import sqlite3
import datetime
import requests
from pathlib import Path

def sync_threads():
    print("[Threads Sync] Starting...")
    tokens_path = Path(__file__).parent / "threads_tokens.json"
    if not tokens_path.exists():
        print("[Threads Sync] No Threads credentials found. Skipping.")
        return
        
    try:
        with open(tokens_path, "r") as f:
            tokens = json.load(f)
    except Exception as e:
        print(f"[Threads Sync] Failed to read tokens: {e}")
        return

    access_token = tokens.get("access_token")
    if not access_token:
        print("[Threads Sync] No access token found in tokens file. Skipping.")
        return

    # 1. Fetch Follower Count & Views from Insights API
    followers = 0
    views = 0
    
    try:
        resp = requests.get(
            "https://graph.threads.net/v1.0/me/threads_insights",
            params={
                "metric": "followers_count,views",
                "access_token": access_token
            },
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            metrics_list = data.get("data", [])
            for m in metrics_list:
                name = m.get("name")
                values = m.get("values", [])
                if values:
                    val = values[0].get("value", 0)
                    if name == "followers_count":
                        followers = int(val)
                    elif name == "views":
                        views = int(val)
            print(f"[Threads Sync] Fetched from Insights: followers={followers}, views={views}")
        else:
            print(f"[Threads Sync] Insights API returned status code {resp.status_code}: {resp.text}")
            print("[Threads Sync] Warning: Please re-authorize Threads in settings to grant 'threads_manage_insights' scope.")
    except Exception as e:
        print(f"[Threads Sync] Failed to fetch insights: {e}")

    # Connect to database
    db_path = Path(__file__).parent / "analytics.db"
    conn = sqlite3.connect(db_path, timeout=30.0)
    c = conn.cursor()
    
    today_str = datetime.date.today().isoformat()
    
    # 2. Save follower count to channel_insights
    try:
        c.execute("CREATE TABLE IF NOT EXISTS channel_insights (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, date TEXT, followers INTEGER, profile_views INTEGER, website_clicks INTEGER)")
        c.execute("DELETE FROM channel_insights WHERE platform = ? AND date = ?", ("threads", today_str))
        c.execute("INSERT INTO channel_insights (platform, date, followers) VALUES (?, ?, ?)", ("threads", today_str, followers))
        print(f"[Threads Sync] Saved channel followers: {followers}")
    except Exception as e:
        print(f"[Threads Sync] Failed to save channel insights: {e}")
        
    # 3. Save snapshot for the timeline
    try:
        c.execute("CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_date TEXT, song_name TEXT, platform TEXT, views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER)")
        c.execute("DELETE FROM snapshots WHERE platform = ? AND snapshot_date = ? AND song_name = ?", ("threads", today_str, ""))
        c.execute("INSERT INTO snapshots (snapshot_date, song_name, platform, views) VALUES (?, ?, ?, ?)", (today_str, "", "threads", views))
        print(f"[Threads Sync] Saved snapshot views: {views}")
    except Exception as e:
        print(f"[Threads Sync] Failed to save snapshot: {e}")

    conn.commit()
    conn.close()
    print("[Threads Sync] Done!")

if __name__ == "__main__":
    sync_threads()
