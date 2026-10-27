#!/usr/bin/env python3
"""
ArrangeMe Upload Automation Bot
Automates publishing arrangements to Hal Leonard's ArrangeMe portal.
Supports automatic YouTube link detection, difficulty level selection, and dual upload (Original + Easy).
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Ensure stdout handles UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load credentials from .env
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

ARRANGEME_EMAIL = os.getenv("ARRANGEME_EMAIL")
ARRANGEME_PASSWORD = os.getenv("ARRANGEME_PASSWORD")

PROFILE_DIR = Path(__file__).resolve().parent / ".arrangeme_profile"
SCORES_DIR = Path(r"C:\Dev\meloscribe\Scores")
PACKAGES_DIR = Path(r"C:\Dev\meloscribe\packages")
PREVIEWS_DIR = Path(r"C:\Dev\meloscribe\mp3 preview")
WEBSITE_AUDIO_DIR = Path(r"C:\Dev\meloscribe-frontend\website\public\audio-previews")
ANALYTICS_DB_PATH = Path(r"C:\Dev\meloscribe-app\tools\meloscribe\backend\analytics.db")
SONGS_JSON_PATH = Path(r"C:\Dev\meloscribe-app\tools\meloscribe\backend\songs.json")
SONGS_JSON_FALLBACK = Path(r"C:\Dev\meloscribe-frontend\website\src\data\songs.json")

# ArrangeMe verified internal Genre IDs (matched against live portal select#genres):
ALL_ARRANGEME_GENRES = {
    "19th century": "38",
    "20th century": "1",
    "21st century": "61",
    "a cappella": "37",
    "advent": "60",
    "barbershop": "53",
    "baroque": "2",
    "big band": "68",
    "blues": "3",
    "bollywood": "45",
    "celtic/irish": "4",
    "celtic": "4",
    "irish": "4",
    "chamber": "46",
    "children": "5",
    "christian": "54",
    "christmas": "6",
    "classical": "7",
    "comedy": "47",
    "contemporary": "8",
    "contemporary a cappella": "69",
    "contest/festival": "9",
    "country": "10",
    "disco": "55",
    "early music": "39",
    "easter": "11",
    "film/tv": "12",
    "film": "12",
    "soundtrack": "12",
    "movie": "12",
    "tv": "12",
    "folk": "13",
    "funk": "56",
    "gospel": "73",
    "graduation": "72",
    "grunge": "48",
    "halloween": "64",
    "hanukkah": "14",
    "hindi": "40",
    "hip-hop": "65",
    "hip hop": "65",
    "historic": "41",
    "holiday": "15",
    "instructional": "16",
    "jazz": "17",
    "jewish": "18",
    "latin": "19",
    "lent": "63",
    "marathi": "42",
    "march": "49",
    "mariachi": "62",
    "medieval": "50",
    "musical/broadway": "20",
    "musical": "20",
    "broadway": "20",
    "new age": "21",
    "opera": "22",
    "patriotic": "23",
    "pop": "24",
    "praise & worship": "25",
    "punk": "57",
    "r&b": "66",
    "ragtime": "51",
    "reggae": "58",
    "religious": "52",
    "renaissance": "28",
    "rock": "29",
    "romantic period": "27",
    "romantic": "27",
    "sacred": "30",
    "show choir": "71",
    "singer/songwriter": "43",
    "ska": "67",
    "soul": "59",
    "spiritual": "31",
    "standards": "32",
    "swing": "70",
    "thriller": "44",
    "traditional": "33",
    "video game": "34",
    "wedding": "35",
    "world/multicultural": "36",
    "electronic": "8",
}
GENRE_MAP = ALL_ARRANGEME_GENRES

def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] [ArrangeMe Bot] {msg}", flush=True)

def classify_genres_with_ai(song_name: str, artist: str = "") -> list[tuple[str, str]]:
    """
    Leverages Gemini 3.8 Flash to classify ALL fitting genres (between 1 and 3 categories)
    for a song from ArrangeMe's official genre taxonomy.
    Supports retry with progressive backoff on 429 and fallback to Gemini 2.5 Flash.
    Returns: list of (genre_name, genre_code) tuples, e.g. [('Pop', '24'), ('Contemporary', '8')]
    """
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    if not gemini_key:
        settings_path = Path(__file__).resolve().parent / "meloscribe" / "backend" / "settings.json"
        if settings_path.exists():
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    gemini_key = json.load(f).get("gemini_api_key", "")
            except Exception:
                pass

    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            
            prompt = (
                f"You are an expert music cataloger. For the piano arrangement of the song '{song_name}' "
                f"by '{artist}', select ALL fitting genres (between 1 and 3 categories, ordered from most relevant to least) "
                f"from this complete list of valid ArrangeMe categories:\n"
                f"Pop, Rock, Classical, Film/TV, Contemporary, Folk, Jazz, Blues, Country, R&B, Soul, Hip-Hop, "
                f"New Age, Video Game, Wedding, Holiday, Christmas, Musical/Broadway, Singer/Songwriter, Disco, "
                f"Funk, Reggae, Punk, Grunge, Latin, Celtic/Irish, Standards, Spiritual, Gospel, Traditional.\n"
                f"Return ONLY a comma-separated list of the matching category names from the list (e.g. 'Pop, Contemporary' or 'Film/TV, New Age'). Nothing else."
            )
            
            # Try Gemini 3.8 Flash with fallback to 2.5 Flash
            models_to_try = ["models/gemini-3.8-flash", "models/gemini-2.5-flash", "models/gemini-flash-latest"]
            res_text = ""
            for m_name in models_to_try:
                try:
                    m = genai.GenerativeModel(m_name)
                    res = m.generate_content(prompt)
                    res_text = res.text.strip()
                    if res_text:
                        break
                except Exception as model_err:
                    if "429" in str(model_err) or "Quota" in str(model_err):
                        time.sleep(2)
                        continue
                    else:
                        continue
            
            if res_text:
                matched = []
                tokens = [t.strip().lower() for t in res_text.split(",") if t.strip()]
                for t in tokens:
                    for g_name, g_code in ALL_ARRANGEME_GENRES.items():
                        if t == g_name or t in g_name:
                            matched.append((g_name.title(), g_code))
                            break
                if matched:
                    # Deduplicate while preserving order, max 3
                    seen = set()
                    unique_matched = []
                    for name, code in matched:
                        if code not in seen:
                            seen.add(code)
                            unique_matched.append((name, code))
                    if unique_matched:
                        names_str = ", ".join([n for n, _ in unique_matched[:3]])
                        codes_str = ", ".join([c for _, c in unique_matched[:3]])
                        log(f"AI Agent (Gemini 3.8) classified '{song_name}' by '{artist}' as Genres: {names_str} (ArrangeMe Codes: {codes_str})")
                        return unique_matched[:3]
        except Exception as e:
            log(f"Notice: AI multi-genre classification fallback ({e})")

    # Heuristic fallback based on known artists/keywords
    s_lower = song_name.lower()
    a_lower = artist.lower()
    fallback_genres = []
    
    if any(k in a_lower or k in s_lower for k in ["rammstein", "linkin park", "metallica", "nirvana", "rock", "metal"]):
        fallback_genres.append(("Rock", ALL_ARRANGEME_GENRES["rock"]))
    if any(k in a_lower or k in s_lower for k in ["einaudi", "yiruma", "chopin", "beethoven", "bach", "mozart", "debussy", "satie"]):
        fallback_genres.append(("Classical", ALL_ARRANGEME_GENRES["classical"]))
    if any(k in a_lower or k in s_lower for k in ["interstellar", "zimmer", "soundtrack", "theme", "film", "movie", "williams"]):
        fallback_genres.append(("Film/TV", ALL_ARRANGEME_GENRES["film/tv"]))
    if any(k in a_lower or k in s_lower for k in ["minecraft", "zelda", "mario", "undertale", "c418"]):
        fallback_genres.append(("Video Game", ALL_ARRANGEME_GENRES["video game"]))
    if any(k in a_lower or k in s_lower for k in ["christmas", "jingle", "silent night", "carol", "xmas", "noel"]):
        fallback_genres.append(("Holiday", ALL_ARRANGEME_GENRES["holiday"]))
        fallback_genres.append(("Christmas", ALL_ARRANGEME_GENRES["christmas"]))

    if not fallback_genres:
        fallback_genres.append(("Pop", ALL_ARRANGEME_GENRES["pop"]))
        fallback_genres.append(("Contemporary", ALL_ARRANGEME_GENRES["contemporary"]))

    names_str = ", ".join([n for n, _ in fallback_genres])
    codes_str = ", ".join([c for _, c in fallback_genres])
    log(f"Defaulting to genres: {names_str} (ArrangeMe Codes: {codes_str}) for '{song_name}'.")
    return fallback_genres

def normalize_youtube_url(url: str) -> str:
    """Converts youtu.be shortlinks to standard https://www.youtube.com/watch?v= URLs."""
    if not url:
        return ""
    m = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url.strip()

def get_catalog_metadata(song_name: str) -> dict:
    """Looks up artist, difficulty, and hasEasy from songs.json or analytics.db if available."""
    s_clean = song_name.strip().lower()
    
    # 1. Search in songs.json candidates
    candidates = [
        SONGS_JSON_PATH,
        SONGS_JSON_FALLBACK,
        Path(r"C:\Dev\meloscribe-backend\tools\meloscribe\backend\songs.json")
    ]
    for p in candidates:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    songs = json.load(f)
                for s in songs:
                    if s.get("title", "").strip().lower() == s_clean:
                        return s
            except Exception as e:
                log(f"Notice reading {p}: {e}")
                
    # 2. Fallback to analytics.db if not found in songs.json
    if ANALYTICS_DB_PATH.exists():
        try:
            conn = sqlite3.connect(ANALYTICS_DB_PATH)
            cur = conn.cursor()
            cur.execute("""
                SELECT author FROM videos 
                WHERE LOWER(song_name) = ? AND author IS NOT NULL AND author != '' 
                LIMIT 1
            """, (s_clean,))
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                return {"artist": row[0]}
        except Exception:
            pass
            
    return {}


def find_youtube_url(song_name: str, difficulty: str = "Original") -> str:
    """
    Finds the official YouTube performance video link for a song from analytics.db or songs.json.
    Strictly differentiates between Easy and Original:
    - Easy: Matches videos with 'Easy' in title/song_name
    - Original: Excludes videos with 'Easy'
    
    Filters:
    - Excludes 'slow' (slow tutorials), 'teaser', and 'hook' snippets
    - Does NOT exclude performance covers merely because they have '#shorts' in the title
    - Prioritizes full covers and performance videos over tutorials
    """
    is_easy = difficulty.lower() == "easy"
    target_song = song_name.strip().lower()

    # 1. First check songs.json for explicit youtube link if present
    meta = get_catalog_metadata(song_name)
    if is_easy and meta.get("easyYoutubeUrl"):
        return normalize_youtube_url(meta["easyYoutubeUrl"])
    elif not is_easy and meta.get("youtubeUrl"):
        return normalize_youtube_url(meta["youtubeUrl"])
        
    if not ANALYTICS_DB_PATH.exists():
        return ""
    
    try:
        conn = sqlite3.connect(ANALYTICS_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT song_name, title, format, duration_sec, url 
            FROM videos 
            WHERE platform = 'youtube' AND url IS NOT NULL AND url != ''
        """)
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        log(f"Notice querying analytics.db for YouTube link: {e}")
        return ""
        
    candidates = []
    
    for s_name, title, fmt, duration, url in rows:
        t_clean = (title or "").lower()
        s_clean = (s_name or "").lower()
        
        # Negative filter: strictly exclude teaser clips and hooks (< 25 sec)
        if any(kw in t_clean for kw in ["teaser", "hook"]) or (duration and duration < 25.0):
            continue
            
        # Target song must match either song_name or title
        if target_song not in s_clean and target_song not in t_clean:
            continue
            
        # Differentiate Easy vs Original
        title_has_easy = "easy" in t_clean or "easy" in s_clean
        if is_easy:
            if not title_has_easy:
                continue
        else:
            if title_has_easy:
                continue

        # In Meloscribe, "tutorial" or "slow" in the title denotes the slowed-down practice video.
        # ArrangeMe specifically requests the official "Performance Video" (the full-speed piano cover).
        is_slow_tutorial = "slow" in t_clean or "tutorial" in t_clean
        
        # Scoring: Pure performance covers receive top tier priority (score 100), slow tutorials score 10
        score = 100 if not is_slow_tutorial else 10
        if "piano cover" in t_clean or "cover" in t_clean:
            score += 20
        if "piano" in t_clean:
            score += 5

        candidates.append((normalize_youtube_url(url), title, duration or 0, score))
            
    if not candidates:
        return ""
        
    # Pick highest priority score (performance cover > tutorial), then longest duration
    candidates.sort(key=lambda x: (x[3], x[2]), reverse=True)
    return candidates[0][0]

def resolve_song_files(song_name: str, difficulty: str = "Original"):
    """
    Resolves PDF score and MP3 audio preview file paths.
    Strictly keeps Easy and Original separate to avoid uploading original files under an Easy arrangement.
    """
    is_easy = difficulty.lower() == "easy"
    
    # 1. Resolve PDF Score
    pdf_path = None
    if is_easy:
        pdf_candidates = [
            SCORES_DIR / f"{song_name} easy.pdf",
            SCORES_DIR / f"{song_name} Easy.pdf",
            SCORES_DIR / f"{song_name} - Easy.pdf",
            SCORES_DIR / f"{song_name} - easy.pdf",
            SCORES_DIR / f"{song_name} (Easy).pdf",
            PACKAGES_DIR / song_name / f"{song_name} easy.pdf",
            PACKAGES_DIR / song_name / f"{song_name} Easy.pdf",
            PACKAGES_DIR / f"{song_name} Easy" / f"{song_name} Easy.pdf",
            PACKAGES_DIR / f"{song_name} easy" / f"{song_name} easy.pdf",
        ]
    else:
        pdf_candidates = [
            SCORES_DIR / f"{song_name}.pdf",
            PACKAGES_DIR / song_name / f"{song_name}.pdf",
        ]
    for p in pdf_candidates:
        if p.exists():
            pdf_path = p
            break
            
    # 2. Resolve Audio Preview MP3
    audio_path = None
    if is_easy:
        audio_candidates = [
            PREVIEWS_DIR / f"{song_name} Easy Teaser.mp3",
            PREVIEWS_DIR / f"{song_name} easy teaser.mp3",
            WEBSITE_AUDIO_DIR / f"{song_name} Easy.mp3",
            WEBSITE_AUDIO_DIR / f"{song_name} easy.mp3",
        ]
    else:
        audio_candidates = [
            PREVIEWS_DIR / f"{song_name} Teaser.mp3",
            PREVIEWS_DIR / f"{song_name} teaser.mp3",
            WEBSITE_AUDIO_DIR / f"{song_name}.mp3",
        ]
    for a in audio_candidates:
        if a.exists():
            audio_path = a
            break
            
    return pdf_path, audio_path

def generate_description(title: str, artist: str, is_easy: bool = False) -> str:
    if is_easy:
        desc = (
            f"A simplified arrangement of \"{title}\" by {artist}, transcribed and played by meloscribe.\n\n"
            f"I wanted to capture the iconic melody and atmosphere of the original track in an accessible piano version. "
            f"This arrangement is comfortable to read and play while preserving the signature sound and powerful feeling of the original song.\n\n"
            f"Feel free to listen to the audio preview above or check out my videos (@meloscribe) to hear how it sounds in action. "
            f"Hope you enjoy playing it!"
        )
    else:
        desc = (
            f"An arrangement of \"{title}\" by {artist}, transcribed and played by meloscribe.\n\n"
            f"I wanted to capture the exact vibe and powerful drive of the original track on the piano. This version stays true to the "
            f"original structure from the opening theme all the way to the final chords.\n\n"
            f"Feel free to listen to the audio preview above or check out my videos (@meloscribe) to hear how it sounds in action. "
            f"Hope you enjoy playing it!"
        )
    return desc

def ensure_signed_in(page) -> bool:
    """Verifies ArrangeMe authentication and performs automated sign-in if needed."""
    page.goto("https://www.arrangeme.com/account/dashboard", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # Accept cookie banner if present
    try:
        cookie_btn = page.locator("#cookieAccept")
        if cookie_btn.is_visible(timeout=2000):
            cookie_btn.click()
            log("Cookie consent banner accepted.")
    except Exception:
        pass

    # Check if redirected to signin
    if "/signin" in page.url or page.locator("#email").is_visible():
        log("Session expired or logged out. Performing automatic sign-in...")
        if not ARRANGEME_EMAIL or not ARRANGEME_PASSWORD:
            log("ERROR: ARRANGEME_EMAIL or ARRANGEME_PASSWORD not configured in .env!")
            return False

        email_el = page.locator("#email")
        email_el.click()
        email_el.fill("")
        email_el.press_sequentially(ARRANGEME_EMAIL, delay=20)
        page.wait_for_timeout(300)

        pwd_el = page.locator("#passwordInput")
        pwd_el.click()
        pwd_el.fill("")
        pwd_el.press_sequentially(ARRANGEME_PASSWORD, delay=20)
        page.wait_for_timeout(500)

        page.evaluate("""() => {
            const em = document.querySelector('#email');
            const pw = document.querySelector('#passwordInput');
            if (em) {
                em.dispatchEvent(new Event('input', { bubbles: true }));
                em.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (pw) {
                pw.dispatchEvent(new Event('input', { bubbles: true }));
                pw.dispatchEvent(new Event('change', { bubbles: true }));
            }
            const btn = document.querySelector('button.blueButton, button[type=\"submit\"]');
            if (btn) btn.disabled = false;
        }""")
        page.wait_for_timeout(500)

        submit_btn = page.locator("button.blueButton, button[type='submit']").first
        submit_btn.click()
        log("Submitted credentials. Waiting for dashboard...")
        page.wait_for_url("**/account/dashboard", timeout=25000)
        log("Successfully signed in!")
    return True

def search_catalog_for_song(page, song_name: str, artist: str = "", max_pages: int = 10) -> tuple[str | None, str]:
    """
    Searches the Hal Leonard copyright catalog (/sell/copyrighted/search) for a song.
    
    1. If artist is available, first executes a targeted search query: '{song_name} {artist}'
       which leverages Hal Leonard's catalog index to bring exact matches straight to Page 1.
    2. Automatically paginates through search results up to max_pages using pagination buttons.
    3. If query 1 yields no verified match, falls back to searching '{song_name}' alone
       and paginates through all pages.
       
    Returns:
        (matched_url, first_line) or (None, "")
    """
    log("Navigating to Hal Leonard Copyright Search...")
    page.goto("https://www.arrangeme.com/sell/copyrighted/search", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    target_song = song_name.strip().lower()
    target_artist = artist.strip().lower() if artist else ""
    artist_tokens = [target_artist] if target_artist else []
    if target_artist.startswith("the "):
        artist_tokens.append(target_artist[4:].strip())

    queries_to_try = []
    if artist:
        queries_to_try.append(f"{song_name.strip()} {artist.strip()}")
    queries_to_try.append(song_name.strip())

    search_input = page.locator('#keywordSearch, input[name="q"], input[type="search"]').first
    if search_input.count() == 0:
        log("ERROR: Could not locate catalog search input (#keywordSearch).")
        return None, ""

    for q_idx, query in enumerate(queries_to_try):
        is_fallback = q_idx > 0
        if is_fallback:
            log(f"No match found with specific artist query. Falling back to broad search: '{query}' across pagination...")
        else:
            log(f"Searching catalog with query: '{query}'...")

        search_input.click()
        search_input.fill("")
        search_input.fill(query)
        page.keyboard.press("Enter")
        page.wait_for_timeout(2000)

        for current_page in range(1, max_pages + 1):
            rows = page.locator("tr[data-arrange-url]").all()
            if not rows:
                log(f"Query '{query}' returned 0 results on page {current_page}.")
                break

            log(f"Scanning catalog page {current_page} ({len(rows)} songs listed) for '{song_name}'" + (f" by '{artist}'" if artist else "") + "...")

            for r in rows:
                row_text = r.inner_text().lower()
                if target_song in row_text:
                    if target_artist:
                        # Ensure artist or major token matches row
                        matched_artist = any(token in row_text for token in artist_tokens if len(token) >= 3)
                        if not matched_artist:
                            continue

                    matched_url = r.get_attribute("data-arrange-url")
                    first_line = r.inner_text().splitlines()[0] if r.inner_text() else song_name
                    log(f"Match verified on page {current_page}: {first_line} -> {matched_url}")
                    return matched_url, first_line

            # If not matched on current_page, check if next page button exists
            next_btn = page.locator('li.page-item-next:not(.disabled) button, .page-item-next:not(.disabled) button, li.page-item-next:not(.disabled) a').first
            if next_btn.count() > 0 and next_btn.is_visible():
                old_first_row = rows[0].inner_text() if rows else ""
                log(f"Song not found on page {current_page}. Navigating to page {current_page + 1}...")
                next_btn.click()
                page.wait_for_timeout(1500)
                # Wait for table refresh
                for _ in range(8):
                    new_rows = page.locator("tr[data-arrange-url]").all()
                    if new_rows and (len(new_rows) != len(rows) or new_rows[0].inner_text() != old_first_row):
                        break
                    page.wait_for_timeout(300)
            else:
                log(f"Reached end of search results at page {current_page}.")
                break

    return None, ""

def run_single_upload(
    page,
    song_name: str,
    artist: str = "",
    difficulty: str = "Original",
    price: float = 5.99,
    youtube_url: str = "",
    genre: str = "",
    auto_publish: bool = False
) -> bool:
    """Executes the full ArrangeMe multi-step upload wizard for one arrangement."""
    is_easy = difficulty.lower() == "easy"
    pdf_path, audio_path = resolve_song_files(song_name, difficulty)

    # Auto-resolve artist from catalog metadata if empty
    if not artist:
        meta = get_catalog_metadata(song_name)
        if meta.get("artist"):
            artist = meta.get("artist")
            log(f"Auto-detected artist: '{artist}'")

    # Auto-resolve YouTube performance link if not explicitly provided
    if not youtube_url:
        youtube_url = find_youtube_url(song_name, difficulty)
        if youtube_url:
            log(f"Auto-resolved YouTube performance video: {youtube_url}")
        else:
            log("No matching YouTube performance video found (leaving YouTube field empty).")

    log("=" * 65)
    log(f"Starting upload wizard for '{song_name}' ({'Easy' if is_easy else 'Original'} Version)")
    log(f"Artist:     {artist}")
    log(f"PDF Score:  {pdf_path if pdf_path else 'NOT FOUND'}")
    log(f"Audio MP3:  {audio_path if audio_path else 'NOT FOUND'}")
    log(f"YouTube:    {youtube_url if youtube_url else 'None'}")
    log(f"Price:      ${price:.2f} USD")
    log("=" * 65)

    if not pdf_path or not pdf_path.exists():
        log(f"ERROR: Could not locate PDF score for '{song_name}' ({difficulty})!")
        return False

    # Step 1: Search Hal Leonard Catalog across pages
    matched_url, match_desc = search_catalog_for_song(page, song_name, artist=artist, max_pages=10)

    if matched_url:
        log(f"Navigating to Upload Wizard: https://www.arrangeme.com{matched_url}")
        page.goto(f"https://www.arrangeme.com{matched_url}", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
    else:
        log("=" * 65)
        log(f"ERROR: No matching entry found for song '{song_name}'" + (f" by '{artist}'" if artist else "") + " across catalog pages!")
        log("Upload aborted to prevent incorrect copyright assignment.")
        log("=" * 65)
        return False

    if "/sell/copyrighted/upload/" not in page.url:
        log("Timeout waiting for song selection. Aborting.")
        return False

    log(f"At Upload Wizard: {page.url}")
    page.wait_for_timeout(1500)


    # Step 2: Upload Files (Wizard Step 1)
    log(f"Attaching PDF score: {pdf_path}")
    page.set_input_files("#pdfFile", str(pdf_path))
    page.wait_for_timeout(1000)

    if audio_path and audio_path.exists():
        log(f"Attaching Audio Preview MP3: {audio_path}")
        audio_input = page.locator("#previewMp3File, input[type='file'][name='previewMp3File']").first
        if audio_input.count() > 0:
            audio_input.set_input_files(str(audio_path))
            page.wait_for_timeout(1000)
        else:
            log("Notice: No audio preview file input (#previewMp3File) found on this step (skipping).")
    else:
        log("No audio preview file found (skipping audio upload).")

    log("Checking terms and conditions checkbox...")
    try:
        page.locator("#terms, input[type='checkbox'][name*='terms']").first.check(force=True)
    except Exception:
        page.locator("label[for='terms'], .custom-control-label").first.click(force=True)
    page.wait_for_timeout(500)

    log("Submitting Step 1 (Save and Continue)...")
    step1_btn = page.locator('input.blueButton[type="submit"], button.blueButton, input[type="submit"]').first
    step1_btn.click()

    # Step 3: Title Details (Wizard Step 2)
    log("Waiting for Title Details page...")
    page.wait_for_url("**/sell/copyrighted/sheet/details**", timeout=30000)
    page.wait_for_timeout(1500)

    log("Setting Arranger to 'meloscribe'...")
    arranger_input = page.locator('input[name="title.contributors.arrangers"], #title\\.contributors\\.arrangers').first
    arranger_input.fill("meloscribe")
    page.wait_for_timeout(500)

    # Inject Description (Non-blocking DOM and TinyMCE injection)
    desc_text = generate_description(song_name, artist, is_easy=is_easy)
    log(f"Injecting Description ({len(desc_text)} characters)...")
    desc_html = "<p>" + desc_text.replace("\n\n", "</p><p>") + "</p>"

    # 1. Non-blocking direct DOM textarea setting (works even if display:none)
    try:
        page.evaluate("""(text) => {
            const ta = document.querySelector('textarea#description, textarea[name="title.description"], textarea[name*="description"]');
            if (ta) {
                ta.value = text;
                ta.dispatchEvent(new Event('input', { bubbles: true }));
                ta.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }""", desc_text)
    except Exception as e:
        log(f"Notice setting textarea via JS: {e}")

    # 2. Inject directly into TinyMCE editors
    try:
        page.evaluate("""(html) => {
            if (window.tinymce) {
                if (typeof window.tinymce.get === 'function') {
                    const ed = window.tinymce.get('description') || (window.tinymce.editors && window.tinymce.editors[0]);
                    if (ed) {
                        ed.setContent(html);
                        ed.save();
                    }
                }
                if (window.tinymce.activeEditor) {
                    window.tinymce.activeEditor.setContent(html);
                    window.tinymce.activeEditor.save();
                }
            }
        }""", desc_html)
    except Exception as e:
        log(f"TinyMCE eval notice: {e}")

    # 3. Non-blocking iframe fallback if TinyMCE body exists
    try:
        for frame in page.frames:
            if "description" in frame.name.lower() or "ifr" in frame.name.lower():
                frame.evaluate(f"if (document.body) {{ document.body.innerHTML = {repr(desc_html)}; }}")
    except Exception:
        pass

    # Fill YouTube link if available
    if youtube_url:
        log(f"Adding YouTube link: {youtube_url}")
        try:
            # First try via JavaScript (instant, no visibility wait)
            link_set = page.evaluate("""(url) => {
                const el = document.querySelector('input#externalLink, input[name="title.externalLink.linkUrl"], input[name*="externalLink"]');
                if (el) {
                    el.value = url;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
            }""", youtube_url)
            
            # If input is directly visible, also fill with Playwright locator
            if link_set:
                log(f"YouTube link set via JS: {youtube_url}")
            else:
                yt_input = page.locator('input#externalLink, input[name="title.externalLink.linkUrl"], input[name*="externalLink"]').first
                if yt_input.count() > 0:
                    yt_input.fill(youtube_url, timeout=2000)
                    log(f"YouTube link set via locator: {youtube_url}")
                else:
                    log("Notice: Could not locate YouTube input#externalLink.")
        except Exception as e:
            log(f"Notice setting YouTube link: {e}")
    else:
        log("No matching YouTube performance video found (leaving YouTube field empty).")

    page.wait_for_timeout(500)

    # Ensure all TinyMCE textareas are synchronized before submit
    try:
        page.evaluate("if (window.tinymce && typeof window.tinymce.triggerSave === 'function') { window.tinymce.triggerSave(); }")
    except Exception:
        pass

    log("Submitting Step 2 (Title Details)...")
    step2_btn = page.locator('input.blueButton[type="submit"], button.blueButton, input[type="submit"], button[type="submit"]:has-text("Save")').first
    step2_btn.click()

    # Step 4: Arrangement Details & Price (Wizard Step 3)
    log("Waiting for Arrangement Details page...")
    try:
        page.wait_for_url("**/sell/copyrighted/sheet/arrangement**", timeout=20000)
    except Exception as nav_err:
        log("Navigation to Step 3 timed out. Checking for form errors...")
        try:
            err_texts = page.locator(".error, .alert-danger, .has-error, .invalid-feedback, span[id*='error']").all_text_contents()
            clean_errs = [t.strip() for t in err_texts if t.strip()]
            if clean_errs:
                log(f"Form validation errors detected: {', '.join(clean_errs)}")
            err_shot = PROJECT_ROOT / "tools" / "arrangeme_step2_error.png"
            page.screenshot(path=str(err_shot))
            log(f"Saved diagnostic screenshot: {err_shot}")
        except Exception:
            pass
        raise nav_err

    page.wait_for_timeout(1500)

    log("Selecting Arrangement Type: 1 (Piano/Keyboard & Voice)...")
    page.select_option("#arrangementGroup", "1")
    page.wait_for_timeout(1000)

    # Arrangement Format: 2 = Easy Piano, 3 = Piano Solo
    format_val = "2" if is_easy else "3"
    format_name = "Easy Piano" if is_easy else "Piano Solo"
    log(f"Selecting Arrangement Format: {format_val} ({format_name})...")
    page.select_option('#arrangementType, select[name*="arrangementType"]', format_val)
    page.wait_for_timeout(500)

    # Difficulty: 2 = Easy Intermediate, 4 = Advanced Intermediate
    diff_val = "2" if is_easy else "4"
    diff_name = "Easy Intermediate" if is_easy else "Advanced Intermediate"
    log(f"Setting Difficulty Level: {diff_val} ({diff_name})...")
    page.select_option("#difficultyLevel", diff_val)
    page.wait_for_timeout(500)

    # Price
    log(f"Setting Price to ${price:.2f} USD...")
    price_input = page.locator('input[name="arrangement.price"], #price').first
    price_input.fill(f"{price:.2f}")
    page.wait_for_timeout(500)

    log("Submitting Step 3 (Arrangement Details)...")
    step3_btn = page.locator('input.blueButton[type="submit"], button.blueButton, input[type="submit"]').first
    step3_btn.click()

    # Step 5: Genres (Wizard Step 4)
    log("Waiting for Genres page...")
    page.wait_for_url("**/sell/copyrighted/sheet/genres**", timeout=30000)
    page.wait_for_timeout(1500)

    # Use explicitly passed genre or classify with Gemini AI agent (up to 3 matching genres)
    target_genre_codes = []
    if genre:
        tokens = [g.strip().lower() for g in genre.split(",") if g.strip()]
        for t in tokens:
            if t in GENRE_MAP:
                target_genre_codes.append(GENRE_MAP[t])
            else:
                for gm_name, gm_code in GENRE_MAP.items():
                    if t == gm_name or t in gm_name or gm_name in t:
                        target_genre_codes.append(gm_code)
                        break
        if target_genre_codes:
            # Deduplicate
            seen = set()
            target_genre_codes = [c for c in target_genre_codes if not (c in seen or seen.add(c))]
            log(f"Using explicitly specified genres: '{genre}' (ArrangeMe codes: {', '.join(target_genre_codes)})")

    if not target_genre_codes:
        ai_genres = classify_genres_with_ai(song_name, artist)
        target_genre_codes = [code for _, code in ai_genres]
        names_str = ", ".join([name for name, _ in ai_genres])
        codes_str = ", ".join(target_genre_codes)
        log(f"Applying AI-selected genres: {names_str} (ArrangeMe codes: {codes_str})...")

    page.select_option('#genres, select[name="genres[]"], select[name="title.genreIds"]', target_genre_codes)
    page.wait_for_timeout(500)

    log("Submitting Step 4 (Genres)...")
    step4_btn = page.locator('#genresForm button[type="submit"], #genresForm .blueButton, button.blueButton:has-text("Save")').first
    step4_btn.click()

    # Step 6: Title Overview & Review
    log("Waiting for Title Overview page...")
    start_nav = time.time()
    while time.time() - start_nav < 30:
        if "/title/" in page.url or "/review/" in page.url:
            break
        page.wait_for_timeout(1000)

    page.wait_for_timeout(2000)

    log("=" * 65)
    log(f"SUCCESS: Arrangement for '{song_name}' ({difficulty}) prepared on ArrangeMe!")
    log(f"Title Page:   {page.url}")
    log(f"Details:")
    log(f"  - Title:        {song_name}")
    log(f"  - Artist:       {artist}")
    log(f"  - Arranger:     meloscribe")
    log(f"  - Difficulty:   {diff_name}")
    log(f"  - Format:       {format_name}")
    log(f"  - Price:        ${price:.2f} USD")
    log(f"  - YouTube:      {youtube_url or 'None'}")
    log("=" * 65)

    if auto_publish:
        log(f"Auto-publish enabled for {difficulty}. Clicking 'Publish'...")
        publish_btn = page.locator('a.blueButton:has-text("Publish"), button:has-text("Publish"), a[href*="/title/publish/"]').first
        publish_btn.click()
        page.wait_for_timeout(4000)
        log(f"Title ({difficulty}) published! Final URL: {page.url}")

    return True

def run_arrangeme_upload(
    song_name: str,
    artist: str = "",
    difficulty: str = "Auto",
    price: float = 5.99,
    youtube_url: str = "",
    genre: str = "",
    headless: bool = False,
    auto_publish: bool = False
) -> bool:
    """
    Main controller for ArrangeMe publishing workflow.
    Supports single uploads ('Original' or 'Easy') as well as dual uploads ('Both' / 'Auto').
    """
    from playwright.sync_api import sync_playwright

    # Auto-detect artist and hasEasy from catalog if not provided
    meta = get_catalog_metadata(song_name)
    if not artist and meta.get("artist"):
        artist = meta.get("artist")
        log(f"Auto-detected artist from catalog: '{artist}'")

    diff_lower = difficulty.lower()
    easy_pdf, _ = resolve_song_files(song_name, "Easy")
    has_easy_catalog = bool(meta.get("hasEasy") or meta.get("difficulty") == "Original / Easy")
    has_easy_score = bool(easy_pdf and easy_pdf.exists())

    if diff_lower in ("both", "original / easy", "all") or (diff_lower in ("auto", "original") and (has_easy_catalog or has_easy_score)):
        if has_easy_score:
            difficulties = ["Original", "Easy"]
            log(f"Dual upload active: detected both Original and Easy arrangements for '{song_name}'.")
        else:
            log(f"Notice: Easy arrangement flagged in catalog for '{song_name}', but no Easy PDF found in Scores/ or packages/. Proceeding with 'Original' only.")
            difficulties = ["Original"]
    elif diff_lower == "easy":
        difficulties = ["Easy"]
    else:
        difficulties = [difficulty]

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        log("Launching Chrome browser with persistent session...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"]
        )

        first_page = context.pages[0] if context.pages else context.new_page()

        # Step 0: Ensure signed in
        if not ensure_signed_in(first_page):
            context.close()
            return False

        results = []
        for idx, diff in enumerate(difficulties):
            # First difficulty uses first_page, subsequent difficulty opens in a new tab
            page = first_page if idx == 0 else context.new_page()
            
            # YouTube URL: if explicit CLI URL was passed for a single upload, use it; otherwise auto-detect per difficulty
            yt = youtube_url if len(difficulties) == 1 else ""

            try:
                success = run_single_upload(
                    page=page,
                    song_name=song_name,
                    artist=artist,
                    difficulty=diff,
                    price=price,
                    youtube_url=yt,
                    genre=genre,
                    auto_publish=auto_publish
                )
                results.append((diff, success))
            except Exception as e:
                import traceback
                log(f"ERROR uploading {diff} arrangement: {e}")
                log(traceback.format_exc())
                try:
                    err_path = PROJECT_ROOT / "tools" / f"arrangeme_error_{diff.lower()}.png"
                    page.screenshot(path=str(err_path))
                    log(f"Diagnostic screenshot captured at: {err_path}")
                except Exception:
                    pass
                results.append((diff, False))

        # Final review handling
        all_ok = all(r[1] for r in results)
        if auto_publish:
            log("All requested arrangements uploaded and published successfully.")
        elif headless:
            log("Headless mode completed: All review pages reached.")
            first_page.wait_for_timeout(5000)
        else:
            log("=" * 65)
            log("Browser window is open with your arrangement tabs ready for review.")
            for diff, ok in results:
                status = "READY" if ok else "FAILED"
                log(f"  - [{status}] {diff} Version")
            log("Click 'Publish' in each tab whenever you are ready.")
            log("Waiting 120 seconds before closing (or close Chrome window when done)...")
            log("=" * 65)
            first_page.wait_for_timeout(120000)

        context.close()
        return all_ok

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload sheet music to ArrangeMe")
    parser.add_argument("--song", default="Sonne", help="Song name (e.g. Golden Brown)")
    parser.add_argument("--artist", default="", help="Original artist (e.g. The Stranglers)")
    parser.add_argument("--difficulty", default="Auto", choices=["Auto", "auto", "Original", "Easy", "Both", "both", "original", "easy"], help="Arrangement difficulty (Auto, Original, Easy, or Both)")
    parser.add_argument("--price", type=float, default=5.99, help="Price in USD")
    parser.add_argument("--youtube", default="", help="Explicit YouTube video URL override")
    parser.add_argument("--genre", default="", help="Genre name (e.g. Rock, Pop)")
    parser.add_argument("--headless", action="store_true", help="Run without UI window")
    parser.add_argument("--auto-publish", action="store_true", help="Automatically click final Publish button")

    args = parser.parse_args()

    ok = run_arrangeme_upload(
        song_name=args.song,
        artist=args.artist,
        difficulty=args.difficulty,
        price=args.price,
        youtube_url=args.youtube,
        genre=args.genre,
        headless=args.headless,
        auto_publish=args.auto_publish
    )
    if not ok:
        sys.exit(1)
