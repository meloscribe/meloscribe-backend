import re
import datetime
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "analytics.db"

def detect_language(title: str) -> str:
    """Auto-detect language based on common characters in the title."""
    title_lower = title.lower()
    
    # French markers
    if any(c in title_lower for c in ['é', 'è', 'ê', 'à', 'ç', 'œ']):
        return 'fr'
        
    # German markers
    if any(c in title_lower for c in ['ä', 'ö', 'ü', 'ß']):
        return 'de'
        
    # Default to English
    return 'en'

def detect_format(title: str, description: str = "") -> str:
    """Detect if this is Standard, Tutorial, Easy, Easy Tutorial, or Hook/Teaser."""
    text = f"{title} {description}".lower()
    is_easy = 'easy' in text
    is_tutorial = 'slow' in text or 'tutorial' in text or 'synthesia' in text
    is_hook = 'hook' in text or 'teaser' in text or 'preview' in text
    
    if is_easy and is_tutorial:
        return 'Easy Tutorial'
    elif is_easy:
        return 'Easy'
    elif is_tutorial:
        return 'Tutorial'
    elif is_hook:
        return 'Hook/Teaser'
    return 'Standard'

def extract_author(title: str, description: str = "") -> str:
    """Try to extract author from 'Song - Author' or 'Song by Author' patterns."""
    text = f"{title} {description}".strip()
    
    # Match "Song - Author" or "Song | Author" ignoring trailing words like tutorial
    m = re.search(r'[-—|][ \t]*([A-Za-z0-9 \t\.\']+?)(?:\s+tutorial|\s+synthesia|\s+cover|\s+piano|\(|\[|$|\n)', title, re.IGNORECASE)
    if m:
        author = m.group(1).strip()
        # Ignore common suffixes that aren't authors if they slipped through
        if author.lower() not in ['piano tutorial', 'synthesia', 'cover', 'sheet music', 'tutorial', 'slow version']:
            return author
            
    # Match "by Author"
    m = re.search(r'\bby[ \t]+([A-Z][A-Za-z0-9 \t\.\']+)', text)
    if m:
        return m.group(1).strip()
        
    return "Unknown"

def get_known_songs(cursor) -> list[str]:
    cursor.execute("SELECT song_name FROM tracks")
    return [row[0] for row in cursor.fetchall()]


def match_song_name(text: str, known_songs: list[str], cursor) -> str | None:
    if not text:
        return None
        
    text_lower = text.lower()
    
    # 1. Check against known songs (sort by length descending to match longest first)
    for song in sorted(known_songs, key=len, reverse=True):
        if song.lower() in text_lower:
            return song
            
    # 2. Not in known_songs? Let's try to auto-extract the base name!
    # Titles are usually "Song Name - Author" or "Song Name | Author"
    # We split by ' - ', ' — ', ' | ', or ' ('. The first part is usually the song.
    m = re.split(r'\s*[-—|]\s*|\s+\(', text)
    if m and len(m[0]) > 2:
        extracted_name = m[0].strip()
        
        # Make sure it's not some generic text or too long
        if len(extracted_name) < 50 and extracted_name.lower() not in ['piano tutorial', 'slow version']:
            print(f"  [Auto-Detect] New song discovered: '{extracted_name}'")
            
            # Auto-insert into tracks table so it's known from now on
            language = detect_language(extracted_name)
            author = extract_author(text)
            try:
                cursor.execute('''
                    INSERT INTO tracks (song_name, author, language)
                    VALUES (?, ?, ?)
                ''', (extracted_name, author, language))
                known_songs.append(extracted_name)
            except Exception as e:
                print(f"  [Auto-Detect] DB Error inserting track: {e}")
                
            return extracted_name
            
    return None

def upsert_video(cursor, video_data: dict):
    """
    video_data expects:
    id, song_name, platform, title, author, language, publish_date, duration_sec,
    views, likes, comments, shares, saves, reach, watch_time_min, avg_view_pct, ctr, url, format
    """
    cursor.execute('''
        INSERT INTO videos (
            id, song_name, platform, title, author, language, publish_date, duration_sec,
            views, likes, comments, shares, saves, reach, watch_time_min, avg_view_pct, ctr, url, format, last_synced
        ) VALUES (
            :id, :song_name, :platform, :title, :author, :language, :publish_date, :duration_sec,
            :views, :likes, :comments, :shares, :saves, :reach, :watch_time_min, :avg_view_pct, :ctr, :url, :format, datetime('now')
        )
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            views = excluded.views,
            likes = excluded.likes,
            comments = excluded.comments,
            shares = excluded.shares,
            saves = excluded.saves,
            reach = excluded.reach,
            watch_time_min = excluded.watch_time_min,
            avg_view_pct = excluded.avg_view_pct,
            ctr = excluded.ctr,
            format = excluded.format,
            last_synced = excluded.last_synced
    ''', video_data)

    # Also record daily snapshot
    today = datetime.datetime.utcnow().strftime('%Y-%m-%d')
    cursor.execute('''
        INSERT OR IGNORE INTO snapshots (video_id, platform, song_name, views, likes, snapshot_date)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (video_data['id'], video_data['platform'], video_data['song_name'], video_data['views'], video_data['likes'], today))


def evaluate_action_triggers(cursor):
    """
    The Action Engine.
    Runs after every sync and evaluates hard rules against the database.
    Inserts actionable To-Dos with priority tags if conditions are met.
    Returns the number of new actions created.
    """
    import datetime as dt
    actions_created = 0
    now_iso = dt.datetime.now().isoformat()

    # ── RULE 1: "Erstelle Long-Form dazu" ─────────────────────────────────────
    # High-retention Short (avg_view_pct > 75% OR save_rate > 1.5%) that has NO tutorial yet.
    cursor.execute('''
        SELECT v.song_name, v.avg_view_pct, v.views, v.saves, v.platform
        FROM videos v
        WHERE v.format = 'Standard'
          AND v.views > 200
          AND (v.avg_view_pct > 75 OR (v.saves * 100.0 / MAX(v.views, 1)) > 1.5)
          AND v.song_name NOT IN (
              SELECT v2.song_name FROM videos v2 WHERE v2.format = 'Tutorial'
          )
        GROUP BY v.song_name
        ORDER BY v.views DESC
        LIMIT 5
    ''')
    for row in cursor.fetchall():
        song, retention, views, saves, platform = row
        tag = f"[PRIORITY] {song} - Full Tutorial"
        cursor.execute("SELECT id FROM todos WHERE song_name=?", (tag,))
        if not cursor.fetchone():
            save_rate = (saves * 100.0 / max(views, 1))
            reason = f"Retention {retention:.0f}%" if retention > 75 else f"Save-Rate {save_rate:.1f}%"
            cursor.execute(
                "INSERT INTO todos (song_name, added_date, status) VALUES (?, ?, 'pending')",
                (tag, now_iso)
            )
            actions_created += 1
            print(f"  [Action Engine] RULE 1: {tag} ({reason}, {views} Views auf {platform})")

    # ── RULE 2: "Format-Shift" ────────────────────────────────────────────────
    # Videos with decent views (>500) but terrible retention (<30%).
    cursor.execute('''
        SELECT v.song_name, v.avg_view_pct, v.views, v.platform
        FROM videos v
        WHERE v.views > 500
          AND v.avg_view_pct > 0
          AND v.avg_view_pct < 30
        GROUP BY v.song_name
        ORDER BY v.views DESC
        LIMIT 5
    ''')
    for row in cursor.fetchall():
        song, retention, views, platform = row
        tag = f"[FORMAT-SHIFT] {song}"
        cursor.execute("SELECT id FROM todos WHERE song_name=?", (tag,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO todos (song_name, added_date, status) VALUES (?, ?, 'pending')",
                (tag, now_iso)
            )
            actions_created += 1
            print(f"  [Action Engine] RULE 2: {tag} (Retention nur {retention:.0f}%, {views} Views auf {platform})")

    # ── RULE 3: "Evergreen Push" ──────────────────────────────────────────────
    # Video older than 6 months that still gets significant views (>1000) but
    # isn't on all platforms yet.
    six_months_ago = (dt.datetime.now() - dt.timedelta(days=180)).isoformat()
    cursor.execute('''
        SELECT v.song_name, v.views, v.platform, v.publish_date
        FROM videos v
        WHERE v.publish_date < ?
          AND v.views > 1000
        GROUP BY v.song_name
        HAVING COUNT(DISTINCT v.platform) < 4
        ORDER BY v.views DESC
        LIMIT 5
    ''', (six_months_ago,))
    for row in cursor.fetchall():
        song, views, platform, pub_date = row
        tag = f"[RE-PURPOSE] {song}"
        cursor.execute("SELECT id FROM todos WHERE song_name=?", (tag,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO todos (song_name, added_date, status) VALUES (?, ?, 'pending')",
                (tag, now_iso)
            )
            actions_created += 1
            print(f"  [Action Engine] RULE 3: {tag} (Evergreen, {views} Views, nur auf {platform})")

    print(f"  [Action Engine] Fertig. {actions_created} neue Aktionen erstellt.")
    return actions_created
