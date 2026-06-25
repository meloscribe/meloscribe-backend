"""
Backfill Sync Script
--------------------
Runs all synchronization scripts in order to backfill the database
with historical analytics data. Usually run once on initial setup.
"""

from tiktok_sync import sync_tiktok
from ig_sync import sync_instagram
from yt_sync import sync_youtube
from fb_sync import sync_facebook

def run_backfill():
    print("========================================")
    print("      MELOSCRIBE ANALYTICS BACKFILL     ")
    print("========================================")
    
    sync_youtube()
    print("----------------------------------------")
    sync_instagram()
    print("----------------------------------------")
    sync_tiktok()
    print("----------------------------------------")
    sync_facebook()
    print("----------------------------------------")
    
    print("[Backfill] Complete! All historical data fetched.")

if __name__ == "__main__":
    run_backfill()
