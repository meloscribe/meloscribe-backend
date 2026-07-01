"""
Database Setup & Migration
--------------------------
Run this once to create/upgrade analytics.db with all needed tables.
Safe to re-run — uses CREATE TABLE IF NOT EXISTS and ALTER TABLE with error handling.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'analytics.db')


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()

    # ── TRACKS (one row per song, enriched from pipeline) ─────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tracks (
        song_name       TEXT PRIMARY KEY,
        theme           TEXT,
        bpm             INTEGER,
        duration_sec    REAL,
        time_signature  TEXT,
        author          TEXT,
        language        TEXT,
        last_rendered   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Migrate: add columns that may not exist in old DB
    for col, coltype in [('author', 'TEXT'), ('language', 'TEXT')]:
        try:
            cursor.execute(f'ALTER TABLE tracks ADD COLUMN {col} {coltype}')
        except Exception:
            pass  # Column already exists

    # ── PERFORMANCE (legacy aggregated — kept for backwards compatibility) ─────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS performance (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        song_name   TEXT,
        platform    TEXT,
        views       INTEGER,
        likes       INTEGER,
        comments    INTEGER,
        shares      INTEGER,
        recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(song_name, platform)
    )
    ''')

    # ── VIDEOS (per-video granular data) ──────────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS videos (
        id              TEXT PRIMARY KEY,
        song_name       TEXT,
        platform        TEXT,
        title           TEXT,
        author          TEXT,
        language        TEXT,
        publish_date    TEXT,
        duration_sec    REAL,
        views           INTEGER DEFAULT 0,
        likes           INTEGER DEFAULT 0,
        comments        INTEGER DEFAULT 0,
        shares          INTEGER DEFAULT 0,
        saves           INTEGER DEFAULT 0,
        reach           INTEGER DEFAULT 0,
        watch_time_min  REAL    DEFAULT 0,
        avg_view_pct    REAL    DEFAULT 0,
        ctr             REAL    DEFAULT 0,
        url             TEXT,
        last_synced     TEXT
    )
    ''')

    # Migrate: add columns that may not exist
    for col, coltype in [
        ('saves', 'INTEGER DEFAULT 0'),
        ('reach', 'INTEGER DEFAULT 0'),
        ('watch_time_min', 'REAL DEFAULT 0'),
        ('avg_view_pct', 'REAL DEFAULT 0'),
        ('ctr', 'REAL DEFAULT 0'),
        ('format', 'TEXT'),
        ('url', 'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE videos ADD COLUMN {col} {coltype}')
        except Exception:
            pass

    # ── SNAPSHOTS (daily view/like snapshots for growth curves) ───────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS snapshots (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id      TEXT,
        platform      TEXT,
        song_name     TEXT,
        views         INTEGER DEFAULT 0,
        likes         INTEGER DEFAULT 0,
        snapshot_date TEXT,
        UNIQUE(video_id, snapshot_date)
    )
    ''')

    # ── SONG TAGS (manual enrichment layer) ───────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS song_tags (
        song_name   TEXT PRIMARY KEY,
        genre       TEXT,
        mood        TEXT,
        difficulty  INTEGER,
        key_sig     TEXT,
        format      TEXT,
        cta_type    TEXT
    )
    ''')

    # ── CHANNEL INSIGHTS (profile-level daily data) ───────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS channel_insights (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        platform        TEXT,
        date            TEXT,
        followers       INTEGER DEFAULT 0,
        profile_views   INTEGER DEFAULT 0,
        website_clicks  INTEGER DEFAULT 0,
        UNIQUE(platform, date)
    )
    ''')

    # ── REVENUE (Ko-Fi webhook data) ──────────────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS revenue (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        amount      REAL,
        currency    TEXT,
        source      TEXT,
        event_type  TEXT,
        buyer       TEXT,
        message     TEXT,
        song_name   TEXT,
        date        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Migrate: add song_name to revenue if needed
    try:
        cursor.execute('ALTER TABLE revenue ADD COLUMN song_name TEXT')
    except Exception:
        pass

    # ── KO-FI MESSAGES ────────────────────────────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS kofi_messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        date        TEXT,
        sender      TEXT,
        amount      REAL,
        message     TEXT,
        is_read     INTEGER DEFAULT 0
    )
    ''')

    # ── TODOS (action plan / song queue) ──────────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS todos (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        song_name   TEXT,
        added_date  TEXT,
        status      TEXT DEFAULT 'pending'
    )
    ''')

    # ── AI REPORTS (cached daily briefings from Gemini) ───────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ai_reports (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date         TEXT,
        recommendation_text TEXT,
        analysis_text       TEXT,
        suggested_songs     TEXT
    )
    ''')
    # ── COMPETITORS (lightweight competitor tracking) ────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS competitors (
        channel_id    TEXT PRIMARY KEY,
        channel_name  TEXT,
        added_date    TEXT
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS competitor_videos (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id    TEXT,
        video_id      TEXT,
        title         TEXT,
        views         INTEGER DEFAULT 0,
        likes         INTEGER DEFAULT 0,
        published_at  TEXT,
        snapshot_date TEXT,
        UNIQUE(video_id, snapshot_date)
    )
    ''')

    # ── PURCHASES (Paddle transaction completions) ───────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS purchases (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT UNIQUE,
        email          TEXT,
        song_name      TEXT,
        amount         REAL,
        currency       TEXT,
        status         TEXT,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        download_hash  TEXT,
        download_count INTEGER DEFAULT 0,
        downloaded_types TEXT DEFAULT '',
        locale         TEXT DEFAULT 'en',
        ip_addresses   TEXT DEFAULT '',
        buyer_name     TEXT DEFAULT ''
    )
    ''')

    # Migrate: add download_hash, download_count, downloaded_types, locale, ip_addresses, and buyer_name to purchases
    for col, coltype in [
        ('download_hash', 'TEXT'),
        ('download_count', 'INTEGER DEFAULT 0'),
        ('downloaded_types', "TEXT DEFAULT ''"),
        ('locale', "TEXT DEFAULT 'en'"),
        ('ip_addresses', "TEXT DEFAULT ''"),
        ('buyer_name', "TEXT DEFAULT ''")
    ]:
        try:
            cursor.execute(f'ALTER TABLE purchases ADD COLUMN {col} {coltype}')
        except Exception:
            pass

    # ── NOTIFY SUBSCRIBERS (email opt-in for new sheet music alerts) ──────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notify_subscribers (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        email      TEXT UNIQUE NOT NULL,
        token      TEXT UNIQUE NOT NULL,
        status     TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        confirmed_at TIMESTAMP
    )
    ''')

    # ── SUGGESTIONS (community sheet music requests) ──────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS suggestions (
        id         TEXT PRIMARY KEY,
        title      TEXT NOT NULL,
        artist     TEXT NOT NULL,
        votes      INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # ── DOWNLOAD IP LOG (rolling IP tracking to prevent sharing) ──────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS download_ip_log (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        purchase_hash  TEXT,
        ip_address     TEXT,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # ── RATE LIMITS (shared-state SQLite backend for workers) ──────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS rate_limits (
        ip          TEXT,
        endpoint    TEXT,
        timestamp   REAL
    )
    ''')
    try:
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_rate_limits_ip_endpoint ON rate_limits(ip, endpoint)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_rate_limits_timestamp ON rate_limits(timestamp)')
    except Exception:
        pass

    conn.commit()
    conn.close()
    print(f"[DB] Analytics DB initialized at {DB_PATH}")


if __name__ == '__main__':
    init_db()
