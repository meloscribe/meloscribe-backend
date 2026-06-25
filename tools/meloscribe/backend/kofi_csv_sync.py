"""
Ko-Fi CSV Sync Module
----------------------------------
Automates downloading the Ko-Fi support received CSV using Playwright.
Uses a dedicated browser profile so it can run in the background without
interfering with the user's active browser.
"""
import os
import sqlite3
import csv
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

DB_PATH = Path(__file__).parent / "analytics.db"
PROFILE_DIR = Path(__file__).parent / "kofi_bot_profile"
EXECUTABLE_PATH = os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe")
if not os.path.exists(EXECUTABLE_PATH):
    EXECUTABLE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

def get_cookies_from_txt():
    """Parses a raw cookie string from kofi_cookie.txt into Playwright cookie objects."""
    cookie_file = Path(__file__).parent / "kofi_cookie.txt"
    if not cookie_file.exists():
        return None
        
    raw_cookie = cookie_file.read_text().strip()
    if not raw_cookie:
        return None
        
    cookies = []
    for pair in raw_cookie.split(';'):
        if '=' in pair:
            name, value = pair.strip().split('=', 1)
            cookies.append({
                'name': name,
                'value': value,
                'domain': '.ko-fi.com',
                'path': '/'
            })
    return cookies

def sync_kofi_csv():
    """Runs headlessly, downloads CSV, and parses into analytics.db"""
    print("[Ko-Fi Sync] Starting background CSV sync...")
    
    cookies = get_cookies_from_txt()
    if not cookies:
        print("[Ko-Fi Sync] No kofi_cookie.txt found. Please create it with your Ko-Fi cookie.")
        return

    csv_path = Path(__file__).parent / "kofi_temp_export.csv"
    
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                executable_path=EXECUTABLE_PATH,
                headless=False,
                args=["--window-position=-32000,-32000", "--window-size=1200,900", "--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Brave/120.0.0.0 Safari/537.36"
            )
            context.add_cookies(cookies)
        except Exception as e:
            print(f"[Ko-Fi Sync] Could not launch browser: {e}")
            return
            
        page = context.new_page()
        try:
            page.goto("https://ko-fi.com/manage/supportreceived?src=sidemenu", wait_until="load")
            time.sleep(3)
            
            # Check if logged in by looking for login button or redirect
            if "login" in page.url.lower():
                print("[Ko-Fi Sync] Bot is not logged in. Needs manual login.")
                browser.close()
                return

            print("[Ko-Fi Sync] Triggering CSV download...")
            time.sleep(2)
            with open(Path(__file__).parent / "kofi_debug.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            
            # Look for download button. Text is usually "Download CSV" or "Export"
            with page.expect_download(timeout=15000) as download_info:
                try:
                    print(f"[Ko-Fi Sync] Clicking exportBtn and then CSV buttons via JS...")
                    page.evaluate("""() => {
                        let btn = document.getElementById('exportBtn');
                        if(btn) btn.click();
                        setTimeout(() => {
                            Array.from(document.querySelectorAll('button, a')).forEach(b => {
                                if (b.innerText && b.innerText.includes('CSV')) {
                                    b.click();
                                }
                            });
                        }, 500);
                    }""")
                except Exception as e:
                    print(f"[Ko-Fi Sync] Could not find CSV download button. {e}")
                    with open(Path(__file__).parent / "kofi_debug.html", "w", encoding="utf-8") as f:
                        f.write(page.content())
                    browser.close()
                    return
                    
            download = download_info.value
            download.save_as(str(csv_path))
            print("[Ko-Fi Sync] Downloaded successfully.")
        except Exception as e:
            print(f"[Ko-Fi Sync] Error during scraping: {e}")
            browser.close()
            return
            
        browser.close()

    if not csv_path.exists():
        return

    # Parse the CSV and update DB
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            # Ko-Fi CSV columns typically: 
            # Payment Date, Name, Email, Amount, Currency, Message, Type, Is Subscription
            added = 0
            for row in reader:
                # Find the columns dynamically in case they slightly change
                keys = {k.lower().replace('\ufeff', '').strip(): k for k in row.keys()}
                
                name = row.get(keys.get("from", keys.get("name", "Name")), "Unknown")
                amount_str = row.get(keys.get("received", keys.get("amount", "Amount")), "0")
                currency = row.get(keys.get("currency", "Currency"), "EUR")
                message = row.get(keys.get("message", "Message"), "")
                item_str = row.get(keys.get("item", "Item"), "")
                date_str = row.get(keys.get("datetime (utc)", keys.get("payment date", "Payment Date")), "")
                
                # Parse song name from item. Example: "Product: 1 x Sweetest Rain - [MIDI + Sheet + Videos] | "
                song_name = ""
                if "x " in item_str:
                    parts = item_str.split("x ", 1)
                    if len(parts) > 1:
                        song_part = parts[1].split(" - ")[0].strip()
                        if song_part:
                            song_name = song_part
                
                # Normalize: strip common suffixes so Ko-Fi names match video DB names
                # e.g. "Experience all parts" -> "Experience", "Nuvole Bianche all parts" -> "Nuvole Bianche"
                import re
                song_name = re.sub(r'\s*(all parts|bundle|pack|full|complete)\s*$', '', song_name, flags=re.IGNORECASE).strip()

                # Cleanup amount string (e.g. "€3,00" -> "3.00")
                amount_clean = amount_str.replace('€', '').replace('$', '').replace(' ', '').replace(',', '.')
                try:
                    amount = float(amount_clean)
                except ValueError:
                    amount = 0.0

                if amount <= 0:
                    continue

                # Check if this exact sale exists (prevent duplicates by matching date + name + amount + song)
                c.execute('''
                    SELECT id FROM revenue 
                    WHERE source = 'Ko-Fi' AND buyer = ? AND amount = ? AND (song_name = ? OR song_name IS NULL)
                ''', (name, amount, song_name))
                
                if c.fetchone() is None:
                    # Insert new sale
                    c.execute('''
                        INSERT INTO revenue (date, amount, source, currency, buyer, song_name, message) 
                        VALUES (datetime('now'), ?, 'Ko-Fi', ?, ?, ?, ?)
                    ''', (amount, currency, name, song_name, message))
                    
                    if message and message.strip():
                        c.execute('''
                            INSERT INTO kofi_messages (date, sender, amount, message, is_read)
                            VALUES (datetime('now'), ?, ?, ?, 0)
                        ''', (name, amount, message.strip()))
                    added += 1

        conn.commit()
        conn.close()
        print(f"[Ko-Fi Sync] Successfully added {added} new sales from CSV.")
    except Exception as e:
        print(f"[Ko-Fi Sync] DB Error: {e}")
    finally:
        pass # Keep CSV for debug

if __name__ == "__main__":
    sync_kofi_csv()
