import sys
import os
import sqlite3
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from yt_sync import sync_youtube
from ig_sync import sync_instagram
from tiktok_sync import sync_tiktok
from fb_sync import sync_facebook
from threads_sync import sync_threads
from pinterest_sync import sync_pinterest

def run_all_syncs():
    print("=== Starting Global Social Media Analytics Sync ===")
    
    # 1. YouTube
    try:
        sync_youtube()
    except Exception as e:
        print(f"[ERROR] YouTube sync failed: {e}")
        
    # 2. Instagram
    try:
        sync_instagram()
    except Exception as e:
        print(f"[ERROR] Instagram sync failed: {e}")
        
    # 3. TikTok
    try:
        sync_tiktok()
    except Exception as e:
        print(f"[ERROR] TikTok sync failed: {e}")
        
    # 4. Facebook
    try:
        sync_facebook()
    except Exception as e:
        print(f"[ERROR] Facebook sync failed: {e}")
        
    # 5. Threads
    try:
        sync_threads()
    except Exception as e:
        print(f"[ERROR] Threads sync failed: {e}")
        
    # 6. Pinterest
    try:
        sync_pinterest()
    except Exception as e:
        print(f"[ERROR] Pinterest sync failed: {e}")
        
    print("=== Sync Complete ===")

if __name__ == "__main__":
    run_all_syncs()
