"""
TikTok Studio Analytics Scraper
---------------------------------
Uses Playwright to scrape analytics data from TikTok Studio web dashboard.
Runs in the background alongside Ko-Fi sync on app startup.
Uses the same Brave browser with a dedicated profile to maintain auth.

Scrapes from: https://www.tiktok.com/tiktokstudio/analytics
"""
import os
import sqlite3
import json
import time
import re
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / "analytics.db"
PROFILE_DIR = Path(__file__).parent / "tiktok_studio_profile"
EXECUTABLE_PATH = os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe")
if not os.path.exists(EXECUTABLE_PATH):
    EXECUTABLE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

STUDIO_URL = "https://www.tiktok.com/tiktokstudio/analytics?tab=overview"
CONTENT_URL = "https://www.tiktok.com/tiktokstudio/analytics?tab=content"


def _parse_number(text: str) -> int:
    """Parse TikTok formatted numbers like '1.2K', '15.3M', '892' into integers."""
    if not text:
        return 0
    text = text.strip().replace(",", "").replace(" ", "")
    multiplier = 1
    if text.upper().endswith("K"):
        multiplier = 1000
        text = text[:-1]
    elif text.upper().endswith("M"):
        multiplier = 1000000
        text = text[:-1]
    elif text.upper().endswith("B"):
        multiplier = 1000000000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


def sync_tiktok_studio():
    """Scrape TikTok Studio analytics overview and per-video stats."""
    print("[TikTok Studio] Starting analytics scrape...")
    
    from settings import load_settings
    settings = load_settings()
    user_data_dir = settings.get("browser_user_data", os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\User Data"))
    executable_path = settings.get("browser_exec", EXECUTABLE_PATH)
    
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        try:
            # Kill any active Brave instances to unlock main profile
            print("Closing active Brave instances to unlock main profile...")
            os.system("taskkill /F /IM brave.exe /T 2>NUL")
            time.sleep(2)
            
            browser = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                executable_path=executable_path,
                headless=False,
                args=[
                    "--profile-directory=Default",
                    "--window-position=-32000,-32000",
                    "--window-size=1400,900",
                    "--disable-blink-features=AutomationControlled"
                ],
                viewport={"width": 1400, "height": 900}
            )
            
            page = browser.pages[0] if browser.pages else browser.new_page()
            
            # Navigate to TikTok Studio overview
            print("[TikTok Studio] Loading overview page...")
            page.goto(STUDIO_URL, wait_until="networkidle", timeout=30000)
            time.sleep(3)
            
            # Check if we're logged in (if redirected to login, abort)
            if "login" in page.url.lower():
                print("[TikTok Studio] Not logged in! Please login manually first:")
                print("  1. Open Brave browser")
                print("  2. Go to https://www.tiktok.com/tiktokstudio/analytics")
                print("  3. Login with your TikTok account")
                print("  4. Run this script again")
                browser.close()
                return False
            
            # Scrape overview metrics from the page
            overview_data = {}
            try:
                # Wait for analytics cards to load
                page.wait_for_selector("[class*='analytics'], [class*='metric'], [class*='card']", timeout=10000)
                time.sleep(2)
                
                # Get all text content from the overview page for parsing
                body_text = page.inner_text("body")
                print(f"[TikTok Studio] Overview page loaded ({len(body_text)} chars)")
                
                # Try to extract follower count and video views from the overview
                # These vary by TikTok's frontend version, so we use flexible selectors
                metrics = page.query_selector_all("[class*='CardContent'], [class*='metric-value'], [class*='data-value']")
                for m in metrics:
                    text = m.inner_text().strip()
                    if text:
                        overview_data[f"metric_{len(overview_data)}"] = text
                        
                print(f"[TikTok Studio] Found {len(overview_data)} overview metrics")
                
            except Exception as e:
                print(f"[TikTok Studio] Could not parse overview: {e}")
            
            # Navigate to Content tab for per-video stats
            print("[TikTok Studio] Loading content tab...")
            page.goto(CONTENT_URL, wait_until="networkidle", timeout=30000)
            time.sleep(3)
            
            # Scrape per-video analytics
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # Get known songs for matching
            known_songs = [r[0] for r in c.execute("SELECT DISTINCT song_name FROM videos").fetchall()]
            
            videos_scraped = 0
            try:
                # Wait for video list to load
                page.wait_for_selector("[class*='video'], [class*='content-card'], table, [class*='VideoList']", timeout=10000)
                time.sleep(2)
                
                # Try to find video rows - TikTok Studio typically shows a table/list
                # Look for video cards or table rows
                video_elements = page.query_selector_all("tr[class*='video'], [class*='video-card'], [class*='VideoItem'], [class*='content-item']")
                
                if not video_elements:
                    # Fallback: try table rows
                    video_elements = page.query_selector_all("table tbody tr")
                
                print(f"[TikTok Studio] Found {len(video_elements)} video elements")
                
                for el in video_elements:
                    try:
                        row_text = el.inner_text()
                        cells = row_text.split("\t") if "\t" in row_text else row_text.split("\n")
                        cells = [c.strip() for c in cells if c.strip()]
                        
                        if len(cells) < 2:
                            continue
                        
                        # First cell is usually the title
                        title = cells[0]
                        
                        # Match to known song
                        matched_song = None
                        title_lower = title.lower()
                        for song in known_songs:
                            if song.lower() in title_lower:
                                matched_song = song
                                break
                        
                        if not matched_song:
                            continue
                        
                        # Parse remaining cells for views, likes, etc.
                        # Order varies but typically: Views, Likes, Comments, Shares
                        numbers = [_parse_number(c) for c in cells[1:] if any(ch.isdigit() for ch in c)]
                        
                        if numbers:
                            views = numbers[0] if len(numbers) > 0 else 0
                            likes = numbers[1] if len(numbers) > 1 else 0
                            comments = numbers[2] if len(numbers) > 2 else 0
                            shares = numbers[3] if len(numbers) > 3 else 0
                            
                            # Update the tiktok video record with scraped data
                            today = datetime.now().strftime("%Y-%m-%d")
                            c.execute("""
                                UPDATE videos SET views=?, likes=?, comments=?, shares=?
                                WHERE platform='tiktok' AND song_name=?
                            """, (views, likes, comments, shares, matched_song))
                            
                            # Also add snapshot
                            c.execute("""
                                INSERT OR REPLACE INTO snapshots 
                                (video_id, song_name, platform, views, likes, snapshot_date)
                                SELECT id, song_name, platform, ?, ?, ?
                                FROM videos WHERE platform='tiktok' AND song_name=?
                                LIMIT 1
                            """, (views, likes, today, matched_song))
                            
                            videos_scraped += 1
                            print(f"  [ok] {matched_song}: {views:,} views")
                    except Exception as e:
                        continue
                        
            except Exception as e:
                print(f"[TikTok Studio] Content tab parse error: {e}")
            
            conn.commit()
            conn.close()
            browser.close()
            
            print(f"[TikTok Studio] Done! Scraped {videos_scraped} videos.")
            return True
            
        except Exception as e:
            print(f"[TikTok Studio] Fatal error: {e}")
            try:
                browser.close()
            except:
                pass
            return False


if __name__ == "__main__":
    sync_tiktok_studio()
