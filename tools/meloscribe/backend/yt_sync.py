"""
YouTube Analytics Sync
----------------------
Pulls video statistics (views, likes, comments) for all tracked videos from the YouTube Data API.
Updates the local `analytics.db`.
"""
import sqlite3
import os
from pathlib import Path
from googleapiclient.discovery import build

try:
    from meloscribe.backend.yt_auth import get_authenticated_service
except ImportError:
    from yt_auth import get_authenticated_service

DB_PATH = Path(__file__).parent / "analytics.db"

def sync_youtube():
    creds = get_authenticated_service()
    if not creds:
        print("[YouTube Sync] No valid auth token found.")
        return

    youtube = build("youtube", "v3", credentials=creds)
    yt_analytics = build("youtubeAnalytics", "v2", credentials=creds)

    import sync_utils
    import datetime

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    known_songs = sync_utils.get_known_songs(cursor)

    # 1. Fetch channel's uploaded videos playlist
    try:
        channel_response = youtube.channels().list(
            part="contentDetails,statistics",
            mine=True
        ).execute()

        if not channel_response.get("items"):
            print("[YouTube Sync] Channel not found.")
            return

        uploads_playlist_id = channel_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        
        # Save followers/subscribers
        try:
            subscribers = 0
            if "statistics" in channel_response["items"][0]:
                subscribers = int(channel_response["items"][0]["statistics"].get("subscriberCount", 0))
            
            today_str = datetime.date.today().isoformat()
            cursor.execute('''
                INSERT INTO channel_insights (platform, date, followers)
                VALUES ('youtube', ?, ?)
                ON CONFLICT(platform, date) DO UPDATE SET
                    followers = excluded.followers
            ''', (today_str, subscribers))
            print(f"[YouTube Sync] Saved channel followers: {subscribers}")
        except Exception as fold_err:
            print(f"[YouTube Sync] Note: Could not save channel subscribers: {fold_err}")

        # 2. Fetch all video IDs
        video_ids = []
        next_page_token = None
        while True:
            playlist_response = youtube.playlistItems().list(
                part="snippet",
                playlistId=uploads_playlist_id,
                maxResults=50,
                pageToken=next_page_token
            ).execute()

            for item in playlist_response.get("items", []):
                video_ids.append(item["snippet"]["resourceId"]["videoId"])

            next_page_token = playlist_response.get("nextPageToken")
            if not next_page_token:
                break

        print(f"[YouTube Sync] Found {len(video_ids)} videos. Fetching stats...")

        # 3. Try fetching Analytics for watch time
        analytics_map = {}
        try:
            today = datetime.date.today().isoformat()
            an_resp = yt_analytics.reports().query(
                ids="channel==MINE",
                startDate="2020-01-01",
                endDate=today,
                metrics="estimatedMinutesWatched,averageViewPercentage",
                dimensions="video",
                sort="-estimatedMinutesWatched",
                maxResults=200
            ).execute()
            
            # Map columns to indices
            col_headers = [h['name'] for h in an_resp.get('columnHeaders', [])]
            if 'video' in col_headers:
                vid_idx = col_headers.index('video')
                min_idx = col_headers.index('estimatedMinutesWatched') if 'estimatedMinutesWatched' in col_headers else -1
                pct_idx = col_headers.index('averageViewPercentage') if 'averageViewPercentage' in col_headers else -1
                
                for row in an_resp.get('rows', []):
                    vid = row[vid_idx]
                    analytics_map[vid] = {
                        "watch_time_min": row[min_idx] if min_idx != -1 else 0,
                        "avg_view_pct": row[pct_idx] if pct_idx != -1 else 0
                    }
        except Exception as e:
            print(f"[YouTube Sync] Note: Analytics API fetch failed ({e}). Proceeding without watch time.")

        # 4. Fetch stats via Data API in chunks of 50
        synced = 0
        skipped = 0
        vid_to_song = {}
        vid_to_format = {}

        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i+50]
            stats_response = youtube.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(chunk)
            ).execute()

            for item in stats_response.get("items", []):
                vid_id = item["id"]
                title = item["snippet"]["title"]
                desc = item["snippet"]["description"]
                published_at = item["snippet"]["publishedAt"]
                duration_iso = item["contentDetails"]["duration"]
                stats = item["statistics"]
                
                import re
                m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_iso)
                duration_sec = 0
                if m:
                    duration_sec = int(m.group(1) or 0)*3600 + int(m.group(2) or 0)*60 + int(m.group(3) or 0)
                
                views = int(stats.get("viewCount", 0))
                likes = int(stats.get("likeCount", 0))
                comments = int(stats.get("commentCount", 0))
                
                song_name = sync_utils.match_song_name(title, known_songs, cursor)
                if not song_name:
                    skipped += 1
                    continue
                    
                author = sync_utils.extract_author(title, desc)
                language = sync_utils.detect_language(title)
                format_type = sync_utils.detect_format(title, desc)
                
                vid_to_song[vid_id] = song_name
                vid_to_format[vid_id] = format_type
                
                an_data = analytics_map.get(vid_id, {})
                
                video_data = {
                    "id": f"yt_{vid_id}",
                    "song_name": song_name,
                    "platform": "youtube",
                    "title": title,
                    "author": author,
                    "language": language,
                    "publish_date": published_at,
                    "duration_sec": duration_sec,
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "shares": 0,
                    "saves": 0,
                    "reach": views, # Approximation for YT
                    "watch_time_min": an_data.get("watch_time_min", 0),
                    "avg_view_pct": an_data.get("avg_view_pct", 0),
                    "ctr": 0,
                    "format": format_type,
                    "url": f"https://youtu.be/{vid_id}"
                }
                
                sync_utils.upsert_video(cursor, video_data)
                synced += 1
                    
        print(f"[YouTube Sync] Done! {synced} videos synced, {skipped} skipped.")

        # 5. Comment Mining / Sentiment Analysis
        try:
            channel_id = channel_response["items"][0]["id"]
            comments_resp = youtube.commentThreads().list(
                part="snippet",
                allThreadsRelatedToChannelId=channel_id,
                maxResults=50,
                order="time"
            ).execute()
            
            tutorial_requests = 0
            for item in comments_resp.get("items", []):
                snippet = item["snippet"]["topLevelComment"]["snippet"]
                text = snippet["textOriginal"].lower()
                vid_id = snippet.get("videoId")
                
                # Keywords indicating a tutorial or sheet request
                if any(kw in text for kw in ["tutorial", "sheet", "please", "how to"]):
                    song_name = vid_to_song.get(vid_id)
                    format_type = vid_to_format.get(vid_id)
                    
                    if song_name and format_type != "Tutorial":
                        target_todo = f"[PRIORITY] {song_name} - Tutorial"
                        c2 = conn.cursor()
                        c2.execute("SELECT id FROM todos WHERE song_name=? AND status='pending'", (target_todo,))
                        if not c2.fetchone():
                            # Also check if the tutorial is already online
                            c2.execute("SELECT id FROM videos WHERE song_name=? AND format='Tutorial'", (song_name,))
                            if not c2.fetchone():
                                c2.execute("INSERT INTO todos (song_name, added_date, status) VALUES (?, ?, 'pending')", (target_todo, datetime.datetime.now().isoformat()))
                                tutorial_requests += 1
                                
            print(f"[YouTube Sync] Found {tutorial_requests} tutorial requests in comments -> added to To-Do.")
        except Exception as e:
            print(f"[YouTube Sync] Error in comment mining: {e}")
            
        conn.commit()

    except Exception as e:
        print(f"[YouTube Sync] Error syncing analytics: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    sync_youtube()
