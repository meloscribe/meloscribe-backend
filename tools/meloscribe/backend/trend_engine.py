import json
import urllib.request
import urllib.error

LASTFM_API_KEY = 'c18bc78a009197198f262ba7e54da9e5'

def get_lastfm_trends():
    url = f'http://ws.audioscrobbler.com/2.0/?method=chart.gettoptracks&api_key={LASTFM_API_KEY}&format=json&limit=20'
    try:
        req = urllib.request.urlopen(url)
        data = json.loads(req.read().decode())
        tracks = [t['name'] + ' by ' + t['artist']['name'] for t in data.get('tracks', {}).get('track', [])]
        return tracks
    except Exception as e:
        print(f"Error fetching Last.fm trends: {e}")
        return []

def get_youtube_trends():
    import sqlite3
    from pathlib import Path
    import os
    
    try:
        import sys
        sys.path.append(str(Path(__file__).parent))
        from yt_auth import get_authenticated_service
        from googleapiclient.discovery import build
        
        creds = get_authenticated_service()
        if not creds:
            return []
            
        youtube = build("youtube", "v3", credentials=creds)
        
        # Get trending music videos in US
        resp = youtube.videos().list(
            part='snippet',
            chart='mostPopular',
            videoCategoryId='10',
            regionCode='US',
            maxResults=20
        ).execute()
        
        if 'items' in resp:
            return [item['snippet']['title'] for item in resp['items']]
    except Exception as e:
        print(f"Error fetching YouTube trends: {e}")
    return []

def get_google_trends():
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl='en-US', tz=360)
        kw_list = ["Piano Sheet", "Piano Tutorial", "Synthesia"]
        pytrends.build_payload(kw_list, cat=0, timeframe='now 7-d', geo='', gprop='youtube')
        data = pytrends.interest_over_time()
        
        if not data.empty:
            latest = data.iloc[-1]
            return {
                "date": str(latest.name.date()),
                "Piano Sheet": int(latest["Piano Sheet"]),
                "Piano Tutorial": int(latest["Piano Tutorial"]),
                "Synthesia": int(latest["Synthesia"])
            }
    except Exception as e:
        print(f"Error fetching Google Trends: {e}")
    return {}

def get_all_trends():
    return {
        "lastfm_top_20": get_lastfm_trends(),
        "youtube_music_trending": get_youtube_trends(),
        "google_search_interest": get_google_trends()
    }

if __name__ == "__main__":
    print(json.dumps(get_all_trends(), indent=2))
