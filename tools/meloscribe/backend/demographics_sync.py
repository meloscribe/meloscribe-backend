"""
Audience Demographics Sync Module
----------------------------------
Pulls demographic data (age, gender, country) from platform APIs.
- YouTube: Analytics API (ageGroup, gender, country dimensions)
- Instagram: Graph API (audience_gender_age, audience_country, audience_city)
- Facebook: DEPRECATED (page_fans_gender_age removed Nov 2025)
- TikTok: NOT AVAILABLE via Content Posting API
"""
import json
import sqlite3
import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "analytics.db"

def _ensure_table(cursor):
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS audience_demographics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT,
        metric_type TEXT,
        metric_key TEXT,
        metric_value REAL,
        snapshot_date TEXT,
        UNIQUE(platform, metric_type, metric_key, snapshot_date)
    )
    ''')

def sync_youtube_demographics():
    """Fetch age, gender, country from YouTube Analytics API."""
    print("[Demographics] Syncing YouTube demographics...")
    try:
        from yt_auth import get_authenticated_service
        from googleapiclient.discovery import build
        
        creds = get_authenticated_service()
        if not creds:
            print("[Demographics] No YouTube credentials.")
            return
        
        analytics = build("youtubeAnalytics", "v2", credentials=creds)
        today = datetime.date.today().isoformat()
        start_date = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
        
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        _ensure_table(c)
        
        # Gender breakdown
        try:
            resp = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date, endDate=today,
                metrics="viewerPercentage",
                dimensions="gender"
            ).execute()
            for row in resp.get("rows", []):
                c.execute("INSERT OR REPLACE INTO audience_demographics (platform, metric_type, metric_key, metric_value, snapshot_date) VALUES (?, ?, ?, ?, ?)",
                         ("youtube", "gender", row[0], row[1], today))
            print(f"  [YouTube] Gender: {len(resp.get('rows', []))} entries")
        except Exception as e:
            print(f"  [YouTube] Gender fetch failed: {e}")
        
        # Age breakdown
        try:
            resp = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date, endDate=today,
                metrics="viewerPercentage",
                dimensions="ageGroup"
            ).execute()
            for row in resp.get("rows", []):
                c.execute("INSERT OR REPLACE INTO audience_demographics (platform, metric_type, metric_key, metric_value, snapshot_date) VALUES (?, ?, ?, ?, ?)",
                         ("youtube", "age", row[0], row[1], today))
            print(f"  [YouTube] Age: {len(resp.get('rows', []))} entries")
        except Exception as e:
            print(f"  [YouTube] Age fetch failed: {e}")
        
        # Country breakdown
        try:
            resp = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date, endDate=today,
                metrics="views",
                dimensions="country",
                sort="-views",
                maxResults=20
            ).execute()
            total_views = sum(row[1] for row in resp.get("rows", []))
            for row in resp.get("rows", []):
                pct = (row[1] / total_views * 100) if total_views > 0 else 0
                c.execute("INSERT OR REPLACE INTO audience_demographics (platform, metric_type, metric_key, metric_value, snapshot_date) VALUES (?, ?, ?, ?, ?)",
                         ("youtube", "country", row[0], round(pct, 2), today))
            print(f"  [YouTube] Country: {len(resp.get('rows', []))} entries")
        except Exception as e:
            print(f"  [YouTube] Country fetch failed: {e}")
        
        conn.commit()
        conn.close()
        print("[Demographics] YouTube demographics synced.")
        
    except Exception as e:
        print(f"[Demographics] YouTube demographics failed: {e}")


def sync_instagram_demographics():
    """Fetch audience demographics from Instagram Graph API."""
    print("[Demographics] Syncing Instagram demographics...")
    try:
        ig_tokens_path = Path(__file__).parent / "ig_tokens.json"
        if not ig_tokens_path.exists():
            print("[Demographics] No Instagram tokens found.")
            return
        
        with open(ig_tokens_path) as f:
            tokens = json.load(f)
        
        access_token = tokens.get("access_token")
        ig_id = tokens.get("ig_business_id")
        if not access_token or not ig_id:
            print("[Demographics] Missing IG credentials.")
            return
        
        import requests
        GRAPH_URL = "https://graph.facebook.com/v19.0"
        today = datetime.date.today().isoformat()
        
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        _ensure_table(c)
        
        # Audience Gender+Age
        try:
            resp = requests.get(
                f"{GRAPH_URL}/{ig_id}/insights",
                params={
                    "metric": "audience_gender_age",
                    "period": "lifetime",
                    "access_token": access_token
                }
            )
            data = resp.json()
            if "data" in data and len(data["data"]) > 0:
                values = data["data"][0].get("values", [{}])[0].get("value", {})
                # values is like {"F.18-24": 5, "M.25-34": 12, ...}
                gender_totals = {}
                age_totals = {}
                total = sum(values.values()) if values else 1
                for key, count in values.items():
                    parts = key.split(".")
                    if len(parts) == 2:
                        gender, age_range = parts
                        gender_name = "female" if gender == "F" else "male" if gender == "M" else "unknown"
                        gender_totals[gender_name] = gender_totals.get(gender_name, 0) + count
                        age_totals[age_range] = age_totals.get(age_range, 0) + count
                
                for g, count in gender_totals.items():
                    pct = round(count / total * 100, 2) if total > 0 else 0
                    c.execute("INSERT OR REPLACE INTO audience_demographics (platform, metric_type, metric_key, metric_value, snapshot_date) VALUES (?, ?, ?, ?, ?)",
                             ("instagram", "gender", g, pct, today))
                
                for a, count in age_totals.items():
                    pct = round(count / total * 100, 2) if total > 0 else 0
                    c.execute("INSERT OR REPLACE INTO audience_demographics (platform, metric_type, metric_key, metric_value, snapshot_date) VALUES (?, ?, ?, ?, ?)",
                             ("instagram", "age", a, pct, today))
                
                print(f"  [Instagram] Gender/Age: {len(gender_totals)} gender + {len(age_totals)} age groups")
        except Exception as e:
            print(f"  [Instagram] Gender/Age fetch failed: {e}")
        
        # Audience Country
        try:
            resp = requests.get(
                f"{GRAPH_URL}/{ig_id}/insights",
                params={
                    "metric": "audience_country",
                    "period": "lifetime",
                    "access_token": access_token
                }
            )
            data = resp.json()
            if "data" in data and len(data["data"]) > 0:
                values = data["data"][0].get("values", [{}])[0].get("value", {})
                total = sum(values.values()) if values else 1
                for country, count in sorted(values.items(), key=lambda x: -x[1])[:20]:
                    pct = round(count / total * 100, 2) if total > 0 else 0
                    c.execute("INSERT OR REPLACE INTO audience_demographics (platform, metric_type, metric_key, metric_value, snapshot_date) VALUES (?, ?, ?, ?, ?)",
                             ("instagram", "country", country, pct, today))
                print(f"  [Instagram] Country: {len(values)} countries")
        except Exception as e:
            print(f"  [Instagram] Country fetch failed: {e}")
        
        # Audience City
        try:
            resp = requests.get(
                f"{GRAPH_URL}/{ig_id}/insights",
                params={
                    "metric": "audience_city",
                    "period": "lifetime",
                    "access_token": access_token
                }
            )
            data = resp.json()
            if "data" in data and len(data["data"]) > 0:
                values = data["data"][0].get("values", [{}])[0].get("value", {})
                total = sum(values.values()) if values else 1
                for city, count in sorted(values.items(), key=lambda x: -x[1])[:15]:
                    pct = round(count / total * 100, 2) if total > 0 else 0
                    c.execute("INSERT OR REPLACE INTO audience_demographics (platform, metric_type, metric_key, metric_value, snapshot_date) VALUES (?, ?, ?, ?, ?)",
                             ("instagram", "city", city, pct, today))
                print(f"  [Instagram] City: {len(values)} cities")
        except Exception as e:
            print(f"  [Instagram] City fetch failed: {e}")
        
        conn.commit()
        conn.close()
        print("[Demographics] Instagram demographics synced.")
        
    except Exception as e:
        print(f"[Demographics] Instagram demographics failed: {e}")


def sync_all_demographics():
    """Run all demographic syncs."""
    sync_youtube_demographics()
    sync_instagram_demographics()
    # Facebook: page_fans_gender_age was deprecated Nov 2025 — no replacement available
    # TikTok: Content Posting API has no demographics endpoint
    print("[Demographics] All available demographics synced.")
