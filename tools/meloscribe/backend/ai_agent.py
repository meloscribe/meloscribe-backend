import google.generativeai as genai
import sqlite3
import json
import os
import sys
import time
import subprocess
import threading
from pathlib import Path
import datetime
from trend_engine import get_all_trends

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def get_gemini_key():
    try:
        for p in (Path(__file__).parent / "settings.json", Path(__file__).parent.parent / "settings.json"):
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                key = settings.get("gemini_api_key")
                if key:
                    return key
    except Exception:
        pass
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

def get_server_api_key():
    try:
        key_path = Path(__file__).resolve().parent / "api_key.txt"
        if key_path.exists():
            return key_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None

GEMINI_API_KEY = get_gemini_key()
genai.configure(api_key=GEMINI_API_KEY)

MODEL_NAME = 'models/gemini-2.5-flash'
FALLBACK_MODEL_NAME = 'models/gemini-flash-latest'
model = genai.GenerativeModel(MODEL_NAME)
fallback_model = genai.GenerativeModel(FALLBACK_MODEL_NAME)

DB_PATH = Path(__file__).parent / "analytics.db"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # C:\Dev\meloscribe-app
TIKTOKS_DIR = Path(r"C:\Dev\meloscribe\TikToks")
SCORES_DIR = Path(r"C:\Dev\meloscribe\Scores")
COVERS_DIR = Path(r"C:\Dev\meloscribe\Covers")
PACKAGES_DIR = Path(r"C:\Dev\meloscribe\packages")
SONGS_JSON_PATH = Path(__file__).parent / "songs.json"

SSH_KEY = r"C:\Dev\ssh-key-2026-05-07.key"
if not os.path.exists(SSH_KEY):
    SSH_KEY = r"C:\Dev\meloscribe\ssh-key-2026-05-07.key"
SERVER_IP = "152.70.23.171"
SERVER_USER = "ubuntu"
SERVER_DIR = "/home/ubuntu/meloscribe"

# Track actions triggered by the agent during conversation turns
CURRENT_TURN_ACTIONS = []

def get_settings():
    try:
        for p in (Path(__file__).parent / "settings.json", Path(__file__).parent.parent / "settings.json"):
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
    except Exception:
        pass
    return {}

def get_python_exe():
    """Locate virtualenv python executable for background subprocesses."""
    venv_python = Path(__file__).parent / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable

def get_catalog():
    """Load song catalog from songs.json."""
    try:
        if SONGS_JSON_PATH.exists():
            with open(SONGS_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[AI Agent] Failed to load catalog: {e}")
    return []

def get_project_context():
    """Extract key guidelines, naming rules and architecture from meloscribe-app-project.md."""
    context_file = PROJECT_ROOT / "meloscribe-app-project.md"
    if not context_file.exists():
        return "meloscribe Desktop App - Sheet music & tutorial automation pipeline."
    try:
        with open(context_file, "r", encoding="utf-8") as f:
            content = f.read()
        # Keep essential portions to avoid token bloating
        lines = content.splitlines()
        extracted = []
        capture = True
        for line in lines:
            if line.startswith("## Completed Milestones") or line.startswith("## Active Roadmap"):
                capture = False
            if capture:
                extracted.append(line)
        return "\n".join(extracted[:160])
    except Exception as e:
        return f"Project documentation read error: {e}"

def fetch_recent_data():
    """Fetch the most critical data from the last 14 days, overall top metrics, live wishlist, and upload queue."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    fourteen_days_ago = (datetime.datetime.now() - datetime.timedelta(days=14)).isoformat()
    
    # Best format
    format_stats = [dict(r) for r in c.execute("SELECT format, AVG(views) as avg_views FROM videos GROUP BY format").fetchall()]
    
    # Best video length
    length_stats = [dict(r) for r in c.execute("SELECT CASE WHEN duration_sec < 61 THEN 'Short' ELSE 'Long' END as length, AVG(views) as avg_views FROM videos GROUP BY length").fetchall()]
    
    # Total Views
    total_views = c.execute("SELECT SUM(views) FROM videos").fetchone()[0] or 0
    
    # Recent top songs (last 14 days publish)
    recent_top = [dict(r) for r in c.execute("SELECT song_name, SUM(views) as views FROM videos WHERE publish_date >= ? GROUP BY song_name ORDER BY views DESC LIMIT 5", (fourteen_days_ago,)).fetchall()]
    
    # Current Todos (Local fallback)
    todos = [dict(r) for r in c.execute("SELECT id, song_name, status FROM todos WHERE status='pending'").fetchall()]
    # Get dismissed suggestions to avoid recommending them again
    dismissed = [r["song_name"] for r in c.execute("SELECT song_name FROM dismissed_suggestions").fetchall()] if "dismissed_suggestions" in [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()] else []
    
    # Purchases & Sales metrics (strictly excluding sandbox test payments)
    purchases_stats = []
    try:
        # Real Stripe purchases (live only, filter out cs_test sandbox transactions)
        stripe_rows = [dict(r) for r in c.execute("""
            SELECT song_name, COUNT(*) as sales_count, SUM(CAST(amount as REAL)) as total_sales 
            FROM purchases 
            WHERE (transaction_id NOT LIKE 'cs_test_%' OR transaction_id IS NULL)
              AND (email != '2beers.sm@gmail.com' OR email IS NULL)
              AND (status NOT LIKE '%refund%' OR status IS NULL)
            GROUP BY song_name
        """).fetchall()]

        # Real Ko-Fi & historical customer revenue from revenue table
        kofi_rows = [dict(r) for r in c.execute("""
            SELECT song_name, COUNT(*) as sales_count, SUM(CAST(amount as REAL)) as total_sales 
            FROM revenue 
            WHERE (message NOT LIKE '%cs_test_%' OR message IS NULL)
              AND (buyer != '2beers.sm@gmail.com' OR buyer IS NULL)
            GROUP BY song_name
        """).fetchall()]

        combined = {}
        for r in kofi_rows:
            sn = r["song_name"] or "Unknown"
            combined[sn] = {"song_name": sn, "sales_count": r["sales_count"], "total_sales": float(r["total_sales"] or 0.0)}
        for r in stripe_rows:
            sn = r["song_name"] or "Unknown"
            if sn in combined:
                combined[sn]["sales_count"] += r["sales_count"]
                combined[sn]["total_sales"] += float(r["total_sales"] or 0.0)
            else:
                combined[sn] = {"song_name": sn, "sales_count": r["sales_count"], "total_sales": float(r["total_sales"] or 0.0)}

        purchases_stats = sorted(list(combined.values()), key=lambda x: x["sales_count"], reverse=True)
    except Exception as e:
        print(f"[AI Agent] Failed to fetch purchases stats: {e}")

    # Overall revenue summary (strictly excluding sandbox test payments)
    revenue_summary = {"total_amount": 0.0, "transactions_count": 0}
    try:
        rev_row = c.execute("""
            SELECT SUM(CAST(amount as REAL)) as total, COUNT(*) as count 
            FROM revenue 
            WHERE (message NOT LIKE '%cs_test_%' OR message IS NULL)
              AND (buyer != '2beers.sm@gmail.com' OR buyer IS NULL)
        """).fetchone()
        if rev_row and rev_row["total"] is not None:
            revenue_summary = {"total_amount": rev_row["total"], "transactions_count": rev_row["count"]}
    except Exception as e:
        print(f"[AI Agent] Failed to fetch revenue stats: {e}")

    # Channel insights (latest followers & profile views for platform views)
    channel_insights = []
    try:
        channel_insights = [dict(r) for r in c.execute("""
            SELECT platform, followers, profile_views, website_clicks, date 
            FROM channel_insights 
            WHERE (platform, date) IN (
                SELECT platform, MAX(date) FROM channel_insights GROUP BY platform
            )
        """).fetchall()]
    except Exception as e:
        print(f"[AI Agent] Failed to fetch channel insights: {e}")

    community_wishlist = []
    upcoming_upload_queue = []
        
    # Live remote metrics retrieval in local mode (Windows)
    import platform
    import urllib.request
    if platform.system() == "Windows":
        settings = get_settings()
        api_key = get_server_api_key() or settings.get("server_api_key")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'x-admin-passcode': settings.get('admin_passcode', '579110')
        }
        if api_key:
            headers['X-Meloscribe-Key'] = api_key
            
        # 1. Live Community Suggestions / Wishlist from Website
        try:
            req = urllib.request.Request(
                "https://api.meloscribe.dev/api/public/suggestions",
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=5.0) as response:
                suggs = json.loads(response.read().decode('utf-8'))
                if isinstance(suggs, list):
                    community_wishlist = [s for s in suggs if s.get("status") != "completed"]
                    community_wishlist.sort(key=lambda x: int(x.get("votes") or 1), reverse=True)
                    # Cache in local SQLite safely
                    try:
                        conn.commit()
                        c.execute("""
                            CREATE TABLE IF NOT EXISTS suggestions (
                                id TEXT PRIMARY KEY,
                                title TEXT NOT NULL,
                                artist TEXT NOT NULL,
                                votes INTEGER DEFAULT 1,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                status TEXT DEFAULT 'open'
                            )
                        """)
                        for sug in suggs:
                            c.execute("""
                                INSERT OR REPLACE INTO suggestions (id, title, artist, votes, created_at, status)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (sug.get("id"), sug.get("title"), sug.get("artist"), sug.get("votes", 1), sug.get("created_at"), sug.get("status", "open")))
                        conn.commit()
                    except Exception as cache_err:
                        print(f"[AI Agent] Suggestions local cache warning: {cache_err}")
        except Exception as err:
            print(f"[AI Agent] Warning: Failed to fetch live suggestions from VM: {err}")
            try:
                rows = c.execute("SELECT id, title, artist, votes, created_at, status FROM suggestions WHERE status != 'completed' OR status IS NULL ORDER BY votes DESC").fetchall()
                community_wishlist = [dict(r) for r in rows]
            except Exception:
                pass

        # 2. Live To-Do List from VM (contains [Wishlist • X Votes] badges)
        try:
            req = urllib.request.Request(
                "https://api.meloscribe.dev/api/todos",
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=5.0) as response:
                vm_todos = json.loads(response.read().decode('utf-8'))
                if isinstance(vm_todos, list) and vm_todos:
                    todos = vm_todos
        except Exception as err:
            print(f"[AI Agent] Warning: Failed to fetch live todos from VM: {err}")

        # 3. Live Analytics
        try:
            req = urllib.request.Request(
                "https://api.meloscribe.dev/api/analytics?range=30d",
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=5.0) as response:
                remote_analytics = json.loads(response.read().decode('utf-8'))
                if "platformBreakdown" in remote_analytics:
                    remote_insights = []
                    for pb in remote_analytics["platformBreakdown"]:
                        remote_insights.append({
                            "platform": pb.get("platform"),
                            "followers": pb.get("followers", 0) or pb.get("likes", 0),
                            "profile_views": pb.get("views", 0),
                            "website_clicks": pb.get("saves", 0),
                            "date": datetime.datetime.now().date().isoformat()
                        })
                    if remote_insights:
                        channel_insights = remote_insights
                if "totals" in remote_analytics and remote_analytics["totals"]:
                    t_views = remote_analytics["totals"].get("v")
                    if t_views is not None:
                        total_views = t_views
        except Exception as err:
            print(f"[AI Agent] Warning: Failed to fetch live analytics from VM: {err}")
            
        # 4. Live Sales
        try:
            req = urllib.request.Request(
                "https://api.meloscribe.dev/api/paddle/sales",
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=5.0) as response:
                sales = json.loads(response.read().decode('utf-8'))
                if isinstance(sales, list):
                    song_sales = {}
                    total_rev = 0.0
                    for sale in sales:
                        # Skip sandbox / test transactions
                        txn_id = str(sale.get("order_id") or sale.get("stripe_order_id") or "")
                        email = str(sale.get("customer_email") or sale.get("buyer") or "")
                        if "cs_test_" in txn_id or "2beers.sm@gmail.com" in email:
                            continue
                        s_name = sale.get("song_name") or "Unknown"
                        amt = float(sale.get("amount") or 0.0)
                        total_rev += amt
                        if s_name not in song_sales:
                            song_sales[s_name] = {"song_name": s_name, "sales_count": 0, "total_sales": 0.0}
                        song_sales[s_name]["sales_count"] += 1
                        song_sales[s_name]["total_sales"] += amt
                    
                    purchases_stats = list(song_sales.values())
                    revenue_summary = {
                        "total_amount": total_rev,
                        "transactions_count": len(sales)
                    }
        except Exception as err:
            print(f"[AI Agent] Warning: Failed to fetch live sales from VM: {err}")

        # 5. Live Upcoming Upload Queue from VM queue.db (pending active queue + recent errors)
        recent_upload_errors = []
        if os.path.exists(SSH_KEY):
            try:
                py_q = "import sqlite3, json; conn=sqlite3.connect('/home/ubuntu/meloscribe/queue.db'); conn.row_factory=sqlite3.Row; print(json.dumps([dict(r) for r in conn.execute('SELECT id, song, author, mode, schedule_time, status, attempts, format FROM upload_queue WHERE status = \\'pending\\' ORDER BY schedule_time ASC LIMIT 15').fetchall()]))"
                cmd = f'ssh -i "{SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=4 ubuntu@{SERVER_IP} "python3 -c \\"{py_q}\\" 2>/dev/null"'
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=6)
                if res.returncode == 0 and res.stdout.strip():
                    upcoming_upload_queue = json.loads(res.stdout.strip())
            except Exception as err:
                pass

            try:
                py_err = "import sqlite3, json; conn=sqlite3.connect('/home/ubuntu/meloscribe/queue.db'); conn.row_factory=sqlite3.Row; print(json.dumps([dict(r) for r in conn.execute('SELECT id, song, mode, schedule_time, status, error FROM upload_queue WHERE status = \\'failed\\' AND datetime(schedule_time) >= datetime(\\'now\\', \\'-2 days\\') ORDER BY schedule_time DESC LIMIT 5').fetchall()]))"
                cmd_err = f'ssh -i "{SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=4 ubuntu@{SERVER_IP} "python3 -c \\"{py_err}\\" 2>/dev/null"'
                res_err = subprocess.run(cmd_err, shell=True, capture_output=True, text=True, timeout=6)
                if res_err.returncode == 0 and res_err.stdout.strip():
                    recent_upload_errors = json.loads(res_err.stdout.strip())
            except Exception as err:
                pass

    # 15 Neueste Video-Uploads chronologisch (allerneueste Uploads zuerst)
    latest_video_uploads = []
    try:
        latest_video_uploads = [dict(r) for r in c.execute("""
            SELECT id, song_name, platform, title, publish_date, duration_sec, 
                   views, likes, comments, shares, saves, format, url 
            FROM videos 
            ORDER BY publish_date DESC 
            LIMIT 15
        """).fetchall()]
    except Exception as e:
        print(f"[AI Agent] Failed to fetch latest video uploads: {e}")

    conn.close()
    return {
        "total_lifetime_views": total_views,
        "latest_video_uploads": latest_video_uploads,
        "format_performance": format_stats,
        "length_performance": length_stats,
        "recent_top_songs": recent_top,
        "current_todo_list": todos,
        "community_wishlist": community_wishlist,
        "upcoming_upload_queue": upcoming_upload_queue,
        "recent_upload_errors": recent_upload_errors,
        "dismissed_suggestions": dismissed,
        "purchases_stats": purchases_stats,
        "revenue_summary": revenue_summary,
        "channel_insights": channel_insights
    }

# -------------------------------------------------------------------
# AGENT TOOL DEFINITIONS (Function Calling)
# -------------------------------------------------------------------

def audit_song_assets(song_name: str = "") -> str:
    """Untersucht lokale Asset-Verzeichnisse (TikToks, Scores, Covers, Packages) auf Vollstaendigkeit.
    
    Args:
        song_name: Optionaler Songname (z.B. 'Sweetest Rain'). Falls leer, wird eine Zusammenfassung geliefert.
    """
    CURRENT_TURN_ACTIONS.append(f"audit_song_assets(song_name='{song_name}')")
    
    if not song_name:
        total_tiktoks = len(list(TIKTOKS_DIR.glob("*.mp4"))) if TIKTOKS_DIR.exists() else 0
        total_scores = len(list(SCORES_DIR.glob("*.pdf"))) if SCORES_DIR.exists() else 0
        catalog = get_catalog()
        return (
            f"Lokale Asset-Uebersicht:\n"
            f"- Songs im Katalog (songs.json): {len(catalog)}\n"
            f"- Fertig gerenderte 9:16 TikTok-Videos: {total_tiktoks} MP4-Dateien\n"
            f"- Fertige Sheet Music Notensaetze: {total_scores} PDF-Dateien\n"
            f"Nenne mir einen konkreten Songnamen, um eine Detail-Pruefung durchzufuehren."
        )
    
    s = song_name.strip()
    s_lower = s.lower()
    
    # 1. Video files
    v_normal = (TIKTOKS_DIR / f"{s}.mp4").exists()
    v_slow = (TIKTOKS_DIR / f"{s} slow.mp4").exists()
    v_easy = (TIKTOKS_DIR / f"{s} easy.mp4").exists() or (TIKTOKS_DIR / f"{s} Easy.mp4").exists()
    v_easy_slow = (TIKTOKS_DIR / f"{s} easy slow.mp4").exists() or (TIKTOKS_DIR / f"{s} Easy slow.mp4").exists()
    
    # 2. Score files
    pdf_normal = (SCORES_DIR / f"{s}.pdf").exists()
    pdf_easy = (SCORES_DIR / f"{s} easy.pdf").exists() or (SCORES_DIR / f"{s} Easy.pdf").exists()
    
    # 3. Covers
    cover_normal = (COVERS_DIR / f"{s}.jpg").exists()
    cover_slow = (COVERS_DIR / f"{s} slow.jpg").exists()
    
    # 4. Packages (Local archive folder - Note: Live web downloads are served directly via Cloudflare R2)
    pkg_dir = PACKAGES_DIR / s
    has_package = pkg_dir.exists() and any(pkg_dir.iterdir())
    
    report = [
        f"Asset-Status fuer '{s}':",
        f"- Normal Video: {'VORHANDEN' if v_normal else 'FEHLT'}",
        f"- Slow Tutorial Video: {'VORHANDEN' if v_slow else 'FEHLT'}",
        f"- Easy Video: {'VORHANDEN' if v_easy else 'Nicht vorhanden/Optional'}",
        f"- Easy Slow Video: {'VORHANDEN' if v_easy_slow else 'Nicht vorhanden/Optional'}",
        f"- PDF Sheet Music: {'VORHANDEN' if pdf_normal else 'FEHLT'}",
        f"- Easy PDF Sheet Music: {'VORHANDEN' if pdf_easy else 'Nicht vorhanden'}",
        f"- Covers (Thumbnail): {'VORHANDEN' if cover_normal else 'FEHLT'} (Slow Cover: {'JA' if cover_slow else 'NEIN'})",
        f"- Lokales Archiv (C:\\Dev\\meloscribe\\packages\\{s}): {'VORHANDEN' if has_package else 'Nicht vorhanden (Nur lokales Offline-Backup - Live-Web-Downloads fuer Kunden laufen zu 100% direkt ueber Cloudflare R2!)'}"
    ]
    return "\n".join(report)

def check_missing_vm_assets() -> str:
    """Vergleicht lokale Video-Renderings in C:\\Dev\\meloscribe\\TikToks mit dem Staging-Verzeichnis auf der Oracle Server VM (152.70.23.171).
    Ermittelt exakt, welche fertig gerenderten Slow-Tutorials oder Normal-Videos noch nicht auf der VM liegen.
    """
    CURRENT_TURN_ACTIONS.append("check_missing_vm_assets()")
    
    if not os.path.exists(SSH_KEY):
        return f"Fehler: SSH Key unter {SSH_KEY} nicht gefunden. Zugriff auf VM nicht moeglich."
        
    cmd = f'ssh -i "{SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 ubuntu@{SERVER_IP} "find /home/ubuntu/meloscribe/staging -type f -name \'*.mp4\'"'
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
    if res.returncode != 0:
        return f"Fehler bei SSH-Abfrage der VM: {res.stderr.strip()}"
        
    staged_files = set()
    for line in res.stdout.strip().splitlines():
        line = line.strip()
        if line:
            staged_files.add(Path(line).name.lower())
            
    # Scan local TikToks
    missing_slow = []
    missing_normal = []
    
    if TIKTOKS_DIR.exists():
        for f in TIKTOKS_DIR.glob("*.mp4"):
            fname_lower = f.name.lower()
            if "teaser" in fname_lower or "wide" in fname_lower:
                continue
            if fname_lower not in staged_files:
                if "slow" in fname_lower:
                    missing_slow.append(f.name)
                else:
                    missing_normal.append(f.name)
                    
    missing_slow.sort()
    missing_normal.sort()
    
    lines = [
        f"VM-Abgleich Staging-Status ({len(staged_files)} Videos bereits auf VM):",
        f"\n1. Fehlende Slow-Tutorial-Videos auf der VM ({len(missing_slow)} Stueck):"
    ]
    if missing_slow:
        for s in missing_slow[:12]:
            lines.append(f"   - {s}")
        if len(missing_slow) > 12:
            lines.append(f"   ... und {len(missing_slow) - 12} weitere.")
    else:
        lines.append("   Keine! Alle lokalen Slow-Versionen sind bereits auf der VM.")
        
    lines.append(f"\n2. Fehlende Normal-Videos auf der VM ({len(missing_normal)} Stueck):")
    if missing_normal:
        for n in missing_normal[:8]:
            lines.append(f"   - {n}")
        if len(missing_normal) > 8:
            lines.append(f"   ... und {len(missing_normal) - 8} weitere.")
    else:
        lines.append("   Keine!")
        
    return "\n".join(lines)

def upload_missing_assets_to_vm(song_name: str, slow_only: bool = True) -> str:
    """Laedt fehlende Video-Dateien fuer einen Song per SCP direkt auf die Oracle VM (/home/ubuntu/meloscribe/staging/[Song]/TikToks) hoch.
    
    Args:
        song_name: Name des Songs (z.B. 'Cornfield Chase' oder 'Sweetest Rain').
        slow_only: Falls True, wird nur die Slow-Version hochgeladen. Falls False, alle lokalen MP4s des Songs.
    """
    CURRENT_TURN_ACTIONS.append(f"upload_missing_assets_to_vm(song_name='{song_name}', slow_only={slow_only})")
    
    s = song_name.strip()
    clean_song = s
    
    # Resolve local files
    candidates = []
    if TIKTOKS_DIR.exists():
        for f in TIKTOKS_DIR.glob(f"{clean_song}*.mp4"):
            fname_lower = f.name.lower()
            if slow_only:
                if "slow" in fname_lower:
                    candidates.append(f)
            else:
                candidates.append(f)
                
    if not candidates:
        return f"Keine lokalen MP4-Dateien fuer '{song_name}' (slow_only={slow_only}) in {TIKTOKS_DIR} gefunden."
        
    # Start upload in background thread to avoid HTTP timeout
    def _do_upload(files_to_upload, s_name):
        try:
            remote_dir = f"/home/ubuntu/meloscribe/staging/{s_name}/TikToks"
            mkdir_cmd = f'ssh -i "{SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 ubuntu@{SERVER_IP} "mkdir -p \\"{remote_dir}\\""'
            subprocess.run(mkdir_cmd, shell=True, capture_output=True)
            
            for file_path in files_to_upload:
                scp_cmd = f'scp -i "{SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "{str(file_path)}" ubuntu@{SERVER_IP}:"\\"{remote_dir}/{file_path.name}\\""'
                print(f"[Agent SCP] Uploading {file_path.name} to VM...")
                res = subprocess.run(scp_cmd, shell=True, capture_output=True, text=True)
                if res.returncode == 0:
                    print(f"[Agent SCP] Successfully uploaded {file_path.name}")
                else:
                    print(f"[Agent SCP] Failed {file_path.name}: {res.stderr}")
        except Exception as err:
            print(f"[Agent SCP] Error during background upload: {err}")

    thread = threading.Thread(target=_do_upload, args=(candidates, clean_song), daemon=True)
    thread.start()
    
    file_list_str = ", ".join([f.name for f in candidates])
    return f"SCP-Upload fuer '{song_name}' gestartet: {file_list_str} wird im Hintergrund auf die VM uebertragen ({SERVER_DIR}/staging/{clean_song}/TikToks)."

def trigger_arrangeme_upload(song_name: str, difficulty: str = "Original", auto_publish: bool = False, headless: bool = False) -> str:
    """Startet den Playwright-Bot fuer das Hal Leonard ArrangeMe Portal (arrangeme_bot.py) im Hintergrund.
    
    Args:
        song_name: Name des Songs (z.B. 'Sonne' oder 'Sweetest Rain').
        difficulty: 'Original' oder 'Easy'.
        auto_publish: Ob nach dem Ausfuellen automatisch 'Publish' geklickt werden soll.
        headless: Ob der Browser unsichtbar (headless) gestartet werden soll.
    """
    CURRENT_TURN_ACTIONS.append(f"trigger_arrangeme_upload(song_name='{song_name}', difficulty='{difficulty}')")
    
    s = song_name.strip()
    
    # Try finding artist from catalog
    catalog = get_catalog()
    artist = ""
    for item in catalog:
        if item.get("title", "").lower() == s.lower():
            artist = item.get("artist", "")
            break
            
    bot_path = PROJECT_ROOT / "tools" / "arrangeme_bot.py"
    if not bot_path.exists():
        return f"Fehler: Bot-Skript {bot_path} nicht gefunden."
        
    cmd = [
        get_python_exe(),
        str(bot_path),
        "--song", s,
        "--difficulty", difficulty
    ]
    if artist:
        cmd.extend(["--artist", artist])
    if auto_publish:
        cmd.append("--auto-publish")
    if headless:
        cmd.append("--headless")
        
    try:
        flags = 0x08000000 if os.name == 'nt' else 0
        subprocess.Popen(cmd, cwd=str(bot_path.parent), creationflags=flags)
        artist_msg = f" by {artist}" if artist else ""
        return (
            f"ArrangeMe-Upload-Modul wurde fuer '{s}'{artist_msg} (Schwierigkeit: {difficulty}) im Hintergrund gestartet! "
            f"Der Browser {'laeuft headless' if headless else 'oeffnet sich'} und traegt Noten, Audio-Preview und Metadaten ein."
        )
    except Exception as e:
        return f"Fehler beim Starten von arrangeme_bot.py: {e}"

def stage_song_to_vm(song_name: str, schedule_date: str = "", schedule_time: str = "16:00", price: str = "6.00") -> str:
    """Fuehrt das vollstaendige Server-Staging (stage_to_server.py) fuer einen Song aus und reiht ihn in die Upload-Queue der VM ein.
    
    Args:
        song_name: Name des Songs.
        schedule_date: Veroeffentlichungsdatum (YYYY-MM-DD). Falls leer, wird das heutige Datum gewaehlt.
        schedule_time: Uhrzeit (z.B. '16:00').
        price: Preis (z.B. '6.00').
    """
    CURRENT_TURN_ACTIONS.append(f"stage_song_to_vm(song_name='{song_name}', date='{schedule_date}')")
    
    s = song_name.strip()
    catalog = get_catalog()
    author = ""
    for item in catalog:
        if item.get("title", "").lower() == s.lower():
            author = item.get("artist", "")
            break
    if not author:
        author = "Unknown"
        
    if not schedule_date:
        schedule_date = datetime.datetime.now().strftime("%Y-%m-%d")
        
    stage_script = PROJECT_ROOT / "tools" / "stage_to_server.py"
    if not stage_script.exists():
        return f"Fehler: Skript {stage_script} nicht gefunden."
        
    cmd = [
        get_python_exe(),
        str(stage_script),
        "--song", s,
        "--author", author,
        "--price", price,
        "--schedule_date", schedule_date,
        "--schedule_time", schedule_time,
        "--platforms", "youtube,instagram,facebook,tiktok,threads"
    ]
    
    try:
        flags = 0x08000000 if os.name == 'nt' else 0
        subprocess.Popen(cmd, cwd=str(stage_script.parent), creationflags=flags)
        return f"Server-Staging fuer '{s}' (Author: {author}, Datum: {schedule_date} {schedule_time}) wurde im Hintergrund gestartet."
    except Exception as e:
        return f"Fehler beim Starten von stage_to_server.py: {e}"

def get_community_wishlist(status: str = "open", limit: int = 15) -> str:
    """Ruft die echte Community-Wishlist und Song-Wuensche von der Website (meloscribe.com / meloscribe.dev) ab.
    Zeigt die von Kunden und Besuchern eingereichten und gevoteten Songs, sortiert nach Anzahl der Stimmen (Votes).
    
    Args:
        status: 'open' (nur offene/ungefertigte Wuensche), 'completed' (bereits erfuellte) oder 'all'.
        limit: Maximale Anzahl der zurueckgegebenen Songs (Standard: 15).
    """
    CURRENT_TURN_ACTIONS.append(f"get_community_wishlist(status='{status}', limit={limit})")
    
    suggs = []
    settings = get_settings()
    api_key = get_server_api_key() or settings.get("server_api_key")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'x-admin-passcode': settings.get('admin_passcode', '579110')
    }
    if api_key:
        headers['X-Meloscribe-Key'] = api_key
        
    import urllib.request
    try:
        req = urllib.request.Request("https://api.meloscribe.dev/api/public/suggestions", headers=headers)
        with urllib.request.urlopen(req, timeout=5.0) as response:
            suggs = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"[AI Agent] Remote suggestions fetch error: {e}")
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            suggs = [dict(r) for r in conn.execute("SELECT id, title, artist, votes, created_at, status FROM suggestions").fetchall()]
            conn.close()
        except Exception:
            pass

    if not suggs:
        return "Aktuell liegen keine Community-Wünsche / Wishlist-Einträge in der Datenbank vor."

    stat = status.lower().strip()
    if stat == "open":
        filtered = [s for s in suggs if s.get("status") != "completed"]
    elif stat == "completed":
        filtered = [s for s in suggs if s.get("status") == "completed"]
    else:
        filtered = list(suggs)

    filtered.sort(key=lambda x: int(x.get("votes") or 1), reverse=True)
    filtered = filtered[:limit]

    if not filtered:
        return f"Keine Community-Vorschläge mit Status '{status}' gefunden."

    lines = [
        f"Community Wishlist / Website-Vorschläge ({len(filtered)} von {len(suggs)} gesamt):",
        f"Hier sind die am meisten gewünschten Songs der Hörer:"
    ]
    for i, s in enumerate(filtered, 1):
        v = s.get("votes", 1)
        t = s.get("title", "Unbekannt")
        a = s.get("artist", "Unbekannt")
        st = s.get("status", "open")
        dt = (s.get("created_at") or "")[:10]
        s_id = s.get("id", "")
        lines.append(f"{i}. [{v} {'Votes' if v != 1 else 'Vote'}] {t} - {a} (Status: {st}, Datum: {dt}, ID: {s_id})")

    return "\n".join(lines)

def manage_community_suggestion(action: str, suggestion_id_or_title: str) -> str:
    """Verwaltet einen Song-Wunsch von der Website. Erlaubt es, einen Wishlist-Song direkt als To-Do zu uebernehmen, als erledigt zu markieren oder zu loeschen.
    
    Args:
        action: 'add_to_todo' (als To-Do anlegen mit Wishlist-Badge), 'complete' (als erledigt markieren) oder 'delete' (loeschen).
        suggestion_id_or_title: Die ID des Eintrags oder der Titel des Songs (z.B. 'Aria math').
    """
    CURRENT_TURN_ACTIONS.append(f"manage_community_suggestion(action='{action}', target='{suggestion_id_or_title}')")
    
    target = suggestion_id_or_title.strip()
    if not target:
        return "Fehler: Kein Songtitel oder keine ID angegeben."
        
    settings = get_settings()
    api_key = get_server_api_key() or settings.get("server_api_key")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'x-admin-passcode': settings.get('admin_passcode', '579110'),
        'Content-Type': 'application/json'
    }
    if api_key:
        headers['X-Meloscribe-Key'] = api_key

    import urllib.request
    suggs = []
    try:
        req = urllib.request.Request("https://api.meloscribe.dev/api/public/suggestions", headers=headers)
        with urllib.request.urlopen(req, timeout=5.0) as response:
            suggs = json.loads(response.read().decode('utf-8'))
    except Exception:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        suggs = [dict(r) for r in conn.execute("SELECT * FROM suggestions").fetchall()]
        conn.close()

    matched = None
    for s in suggs:
        if s.get("id") == target:
            matched = s
            break
        if target.lower() in (s.get("title") or "").lower():
            matched = s
            break

    act = action.lower().strip()
    if act in ("add_to_todo", "convert_to_todo", "todo"):
        if not matched:
            s_name = target
            votes = 1
        else:
            s_name = f"{matched.get('title')} - {matched.get('artist')}" if matched.get('artist') else matched.get('title')
            votes = matched.get("votes", 1)
        
        todo_label = f"[Wishlist • {votes} {'Votes' if votes != 1 else 'Vote'}] {s_name}"
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO todos (song_name, status, added_date) VALUES (?, 'pending', ?)", (todo_label, datetime.datetime.now().isoformat()))
        conn.commit()
        conn.close()

        try:
            req_data = json.dumps({"song_name": todo_label}).encode('utf-8')
            req = urllib.request.Request("https://api.meloscribe.dev/api/todos", data=req_data, headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=5.0)
        except Exception:
            pass

        return f"Wishlist-Song '{s_name}' ({votes} Votes) wurde erfolgreich als To-Do angelegt: '{todo_label}'."

    elif act in ("complete", "done", "mark_completed"):
        sug_id = matched.get("id") if matched else target
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE suggestions SET status='completed' WHERE id=? OR LOWER(title) LIKE ?", (sug_id, f"%{target.lower()}%"))
        conn.commit()
        conn.close()

        if os.path.exists(SSH_KEY):
            cmd = f'ssh -i "{SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 ubuntu@{SERVER_IP} "sqlite3 /home/ubuntu/meloscribe/tools/meloscribe/backend/analytics.db \\"UPDATE suggestions SET status=\'completed\' WHERE id=\'{sug_id}\' OR LOWER(title) LIKE \'%{target.lower()}%\';\\""'
            subprocess.run(cmd, shell=True, capture_output=True, timeout=6)

        return f"Wishlist-Eintrag '{matched.get('title') if matched else target}' wurde als erledigt/completed markiert."

    elif act in ("delete", "remove"):
        sug_id = matched.get("id") if matched else target
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM suggestions WHERE id=? OR LOWER(title) LIKE ?", (sug_id, f"%{target.lower()}%"))
        conn.commit()
        conn.close()

        try:
            req = urllib.request.Request(f"https://api.meloscribe.dev/api/public/suggestions/{sug_id}", headers=headers, method="DELETE")
            urllib.request.urlopen(req, timeout=5.0)
        except Exception:
            pass

        return f"Wishlist-Eintrag '{matched.get('title') if matched else target}' wurde gelöscht."

    return f"Unbekannte Aktion '{action}' für Wishlist. Erlaubt: add_to_todo, complete, delete."

def get_upload_queue(status: str = "all", limit: int = 15) -> str:
    """Ruft die Upload-Warteschlange (Server-Staging queue.db) auf der Oracle Server VM ab.
    Zeigt geplante Veroeffentlichungen inklusive Songname, Social-Media-Plattform (Mode), geplanter Uhrzeit und aktuellem Status (pending, completed, failed, disabled).
    
    Args:
        status: 'all', 'pending', 'completed' oder 'failed'.
        limit: Maximale Anzahl der Eintraege (Standard: 15).
    """
    CURRENT_TURN_ACTIONS.append(f"get_upload_queue(status='{status}', limit={limit})")
    
    if not os.path.exists(SSH_KEY):
        return f"Fehler: SSH Key unter {SSH_KEY} nicht gefunden. Zugriff auf Server-Queue nicht moeglich."

    py_cmd = "import sqlite3, json; conn=sqlite3.connect('/home/ubuntu/meloscribe/queue.db'); conn.row_factory=sqlite3.Row; print(json.dumps([dict(r) for r in conn.execute('SELECT id, song, author, mode, schedule_time, status, error, attempts, format FROM upload_queue ORDER BY schedule_time DESC LIMIT 100').fetchall()]))"
    cmd = [
        "ssh", "-i", SSH_KEY,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=5",
        f"{SERVER_USER}@{SERVER_IP}",
        f'python3 -c "{py_cmd}"'
    ]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return f"Fehler bei SSH-Abfrage der Upload-Queue: {res.stderr.strip()}"
        
        raw = res.stdout.strip()
        if not raw:
            return "Upload-Queue ist leer."
            
        items = json.loads(raw)
        stat = status.lower().strip()
        if stat in ("pending", "completed", "failed", "disabled"):
            items = [it for it in items if (it.get("status") or "").lower() == stat]

        items = items[:limit]
        if not items:
            return f"Keine Upload-Einträge mit Status '{status}' in der Queue gefunden."

        lines = [f"Server Staging & Upload Queue ({len(items)} Einträge, Filter: '{status}'):"]
        for it in items:
            t_id = it.get("id")
            s_name = it.get("song")
            plat = (it.get("mode") or "").upper()
            sched = it.get("schedule_time")
            st = it.get("status")
            err = it.get("error")
            fmt = it.get("format", "full_arrangement")
            
            err_str = f" | FEHLER: {err}" if err and st == "failed" else ""
            lines.append(f"- [ID {t_id}] {sched} → [{plat}] '{s_name}' (Status: {st}, Format: {fmt}){err_str}")

        return "\n".join(lines)
    except Exception as e:
        return f"Fehler beim Abrufen der Upload-Queue: {e}"

def manage_upload_queue(action: str, task_id: int, schedule_time: str = "") -> str:
    """Verwaltet einen Eintrag in der Upload-Warteschlange (queue.db auf der VM).
    
    Args:
        action: 'toggle' (pausieren/aktivieren), 'reschedule' (Datum/Uhrzeit aendern) oder 'delete' (aus Queue entfernen).
        task_id: Die ID des Upload-Tasks in queue.db.
        schedule_time: Neue geplante Zeit im Format 'YYYY-MM-DD HH:MM' (nur fuer action='reschedule').
    """
    CURRENT_TURN_ACTIONS.append(f"manage_upload_queue(action='{action}', task_id={task_id})")
    
    if not os.path.exists(SSH_KEY):
        return f"Fehler: SSH Key unter {SSH_KEY} nicht gefunden."

    act = action.lower().strip()
    if act == "toggle":
        sql = f"UPDATE upload_queue SET status = CASE WHEN status = 'disabled' THEN 'pending' ELSE 'disabled' END WHERE id = {task_id};"
    elif act == "reschedule":
        if not schedule_time:
            return "Fehler: Kein schedule_time angegeben (Format: 'YYYY-MM-DD HH:MM')."
        sql = f"UPDATE upload_queue SET schedule_time = '{schedule_time.strip()}' WHERE id = {task_id};"
    elif act in ("delete", "remove"):
        sql = f"DELETE FROM upload_queue WHERE id = {task_id};"
    else:
        return f"Unbekannte Aktion '{action}'. Moeglich sind: toggle, reschedule, delete."

    cmd = f'ssh -i "{SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 ubuntu@{SERVER_IP} "sqlite3 /home/ubuntu/meloscribe/queue.db \\"{sql}\\""'
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        if res.returncode == 0:
            return f"Upload-Task {task_id} erfolgreich aktualisiert (Aktion: {action})."
        return f"Fehler bei Queue-Aktualisierung: {res.stderr.strip()}"
    except Exception as e:
        return f"Fehler bei Ausfuehrung: {e}"

def inspect_catalog(query: str = "", filter_missing_easy: bool = False, filter_missing_arrangeme: bool = False) -> str:
    """Durchsucht und analysiert den vollstaendigen Notenkatalog (songs.json) im meloscribe Shop.
    Prueft Preise, Schwierigkeitsgrade, Vorhandensein von Easy-Versionen, ArrangeMe-Links und YouTube-Links.
    
    Args:
        query: Optionaler Suchbegriff (Songname, Kuenstler oder Tag).
        filter_missing_easy: Falls True, werden alle Songs aufgelistet, fuer die noch KEINE Easy-Version existiert.
        filter_missing_arrangeme: Falls True, werden alle Songs aufgelistet, fuer die noch kein ArrangeMe-Link hinterlegt ist.
    """
    CURRENT_TURN_ACTIONS.append(f"inspect_catalog(query='{query}', missing_easy={filter_missing_easy}, missing_arrangeme={filter_missing_arrangeme})")
    
    catalog = get_catalog()
    if not catalog:
        return "Notenkatalog (songs.json) ist leer oder konnte nicht geladen werden."

    if filter_missing_easy:
        missing = [s for s in catalog if not s.get("hasEasy") and "easy" not in s.get("difficulty", "").lower()]
        lines = [f"Songs ohne Easy-Version ({len(missing)} von {len(catalog)} Songs):"]
        for s in missing:
            lines.append(f"- {s.get('title')} ({s.get('artist', 'Unknown')}) [Preis: {s.get('price', '6.00')} €]")
        return "\n".join(lines)

    if filter_missing_arrangeme:
        missing = [s for s in catalog if not s.get("arrangemeLink")]
        lines = [f"Songs ohne ArrangeMe-Link ({len(missing)} von {len(catalog)} Songs):"]
        for s in missing:
            lines.append(f"- {s.get('title')} ({s.get('artist', 'Unknown')})")
        return "\n".join(lines)

    q = query.lower().strip()
    if q:
        matches = []
        for s in catalog:
            t = (s.get("title") or "").lower()
            a = (s.get("artist") or "").lower()
            tags = " ".join(s.get("tags") or []).lower()
            if q in t or q in a or q in tags:
                matches.append(s)
        
        if not matches:
            return f"Keine Songs im Katalog gefunden, die zu '{query}' passen."

        lines = [f"Gefundene Songs im Katalog ({len(matches)} Treffer):"]
        for s in matches:
            lines.append(
                f"- **{s.get('title')}** ({s.get('artist', 'Unknown')})\n"
                f"  Preis: {s.get('price', '6.00')} € | Schwierigkeit: {s.get('difficulty', 'Original')}\n"
                f"  Easy Version: {'JA' if s.get('hasEasy') else 'NEIN'}\n"
                f"  ArrangeMe Link: {'VORHANDEN' if s.get('arrangemeLink') else 'FEHLT'}\n"
                f"  YouTube Link: {'VORHANDEN' if s.get('youtubeLink') else 'FEHLT'}\n"
                f"  Tags: {', '.join(s.get('tags') or [])}"
            )
        return "\n".join(lines)

    total = len(catalog)
    has_easy = sum(1 for s in catalog if s.get("hasEasy"))
    has_arrangeme = sum(1 for s in catalog if s.get("arrangemeLink"))
    prices = [float(s.get("price") or 6.0) for s in catalog]
    avg_p = sum(prices) / total if total > 0 else 0.0

    lines = [
        f"Katalog-Zusammenfassung ({total} Songs aktiv im Shop):",
        f"- Durchschnittspreis: {avg_p:.2f} €",
        f"- Mit verfuegbarer Easy-Version: {has_easy} Songs ({(has_easy/total*100):.0f}%)",
        f"- Mit hinterlegtem ArrangeMe-Link: {has_arrangeme} Songs ({(has_arrangeme/total*100):.0f}%)",
        f"\nAktuelle Songs:"
    ]
    for s in catalog[:20]:
        lines.append(f"- {s.get('title')} ({s.get('artist')}) — {s.get('price', '6.00')} € | Easy: {'✓' if s.get('hasEasy') else '✗'}")
    if total > 20:
        lines.append(f"... und {total - 20} weitere Songs.")
    return "\n".join(lines)

def get_recent_sales(limit: int = 10, song_name: str = "") -> str:
    """Ruft die neuesten echten Kundenkaeufe und Sheet-Music-Bestellungen ab (filtert Test- und Sandbox-Zahlungen heraus).
    
    Args:
        limit: Maximale Anzahl der anzuzeigenden Bestellungen (Standard: 10).
        song_name: Optionaler Filter auf einen bestimmten Songnamen.
    """
    CURRENT_TURN_ACTIONS.append(f"get_recent_sales(limit={limit}, song_name='{song_name}')")
    
    settings = get_settings()
    api_key = get_server_api_key() or settings.get("server_api_key")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'x-admin-passcode': settings.get('admin_passcode', '579110')
    }
    if api_key:
        headers['X-Meloscribe-Key'] = api_key

    import urllib.request
    sales = []
    try:
        req = urllib.request.Request("https://api.meloscribe.dev/api/paddle/sales", headers=headers)
        with urllib.request.urlopen(req, timeout=5.0) as response:
            raw_sales = json.loads(response.read().decode('utf-8'))
            if isinstance(raw_sales, list):
                for s in raw_sales:
                    txn = str(s.get("order_id") or s.get("stripe_order_id") or "")
                    em = str(s.get("customer_email") or s.get("buyer") or "")
                    if "cs_test_" in txn or "2beers.sm@gmail.com" in em:
                        continue
                    sales.append(s)
    except Exception as e:
        print(f"[AI Agent] Warning: Failed to fetch sales: {e}")
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT song_name, amount, transaction_id, created_at, status 
                FROM purchases 
                WHERE (transaction_id NOT LIKE 'cs_test_%' OR transaction_id IS NULL)
                  AND (email != '2beers.sm@gmail.com' OR email IS NULL)
                ORDER BY created_at DESC LIMIT ?
            """, (limit * 2,)).fetchall()
            sales = [dict(r) for r in rows]
            conn.close()
        except Exception:
            pass

    if not sales:
        return "Keine echten Kundenkäufe in der Datenbank gefunden."

    if song_name.strip():
        s_query = song_name.lower().strip()
        sales = [s for s in sales if s_query in (s.get("song_name") or "").lower()]

    sales = sales[:limit]
    if not sales:
        return f"Keine Verkäufe für '{song_name}' gefunden."

    lines = [f"Die {len(sales)} neuesten echten Notenverkäufe:"]
    total = 0.0
    for s in sales:
        dt = (s.get("created_at") or s.get("date") or "Unbekannt")[:16].replace("T", " ")
        sn = s.get("song_name") or "Sheet Music"
        amt = float(s.get("amount") or 0.0)
        cur = s.get("currency") or "EUR"
        total += amt
        lines.append(f"- [{dt}] {sn}: {amt:.2f} {cur}")

    lines.append(f"\nSumme der angezeigten Verkäufe: {total:.2f} EUR")
    return "\n".join(lines)

def get_audience_demographics() -> str:
    """Ruft die demografischen Daten der Hörer und Zuschauer (Laender, Alter, Geschlecht) aus analytics.db ab."""
    CURRENT_TURN_ACTIONS.append("get_audience_demographics()")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    try:
        rows = [dict(r) for r in c.execute("SELECT platform, category, label, percentage FROM audience_demographics ORDER BY platform, category, percentage DESC").fetchall()]
        conn.close()
    except Exception as e:
        conn.close()
        return f"Fehler beim Abrufen der Demografie-Daten: {e}"

    if not rows:
        return "Keine demografischen Daten in analytics.db vorhanden."

    lines = ["Demografische Daten der Hörer:"]
    by_plat = {}
    for r in rows:
        p = r.get("platform", "Social")
        cat = r.get("category", "General")
        by_plat.setdefault(f"{p} ({cat})", []).append(f"{r.get('label')}: {r.get('percentage')}%")

    for k, v in by_plat.items():
        lines.append(f"\n**{k}**:\n  " + ", ".join(v[:8]))

    return "\n".join(lines)

def manage_todo(action: str, song_name: str = "") -> str:
    """Verwaltet die Todo-Liste in der Datenbank analytics.db sowie auf der Server-VM.
    
    Args:
        action: 'add' (Song hinzufuegen), 'complete' / 'done' (als erledigt markieren), 'delete' (Song entfernen), 'priority' (als Prioritaet markieren) oder 'list' (aktuelle Todos abrufen).
        song_name: Name des Songs (erforderlich fuer 'add', 'complete', 'delete', 'priority').
    """
    CURRENT_TURN_ACTIONS.append(f"manage_todo(action='{action}', song_name='{song_name}')")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    action = action.lower().strip()
    if action == "list":
        rows = c.execute("SELECT song_name FROM todos WHERE status='pending'").fetchall()
        conn.close()
        if not rows:
            return "Die Todo-Liste ist aktuell leer."
        todos_list = ", ".join([r["song_name"] for r in rows])
        return f"Aktuelle Todos ({len(rows)} Songs): {todos_list}"
        
    elif action == "add":
        if not song_name:
            conn.close()
            return "Fehler: Kein Songname angegeben."
        s = song_name.strip()
        c.execute("INSERT INTO todos (song_name, status, added_date) VALUES (?, 'pending', ?)", (s, datetime.datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        settings = get_settings()
        api_key = get_server_api_key() or settings.get("server_api_key")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'x-admin-passcode': settings.get('admin_passcode', '579110'),
            'Content-Type': 'application/json'
        }
        if api_key:
            headers['X-Meloscribe-Key'] = api_key
        try:
            import urllib.request
            req_data = json.dumps({"song_name": s}).encode('utf-8')
            req = urllib.request.Request("https://api.meloscribe.dev/api/todos", data=req_data, headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=5.0)
        except Exception:
            pass

        return f"Song '{s}' wurde erfolgreich zur Todo-Liste hinzugefuegt."

    elif action in ("complete", "done", "finish"):
        if not song_name:
            conn.close()
            return "Fehler: Kein Songname angegeben."
        s = song_name.strip()
        
        row = c.execute("SELECT id, song_name FROM todos WHERE song_name LIKE ? AND status='pending' LIMIT 1", (f"%{s}%",)).fetchone()
        if not row:
            conn.close()
            return f"Song '{s}' wurde in den offenen To-Dos nicht gefunden."
            
        todo_id = row["id"]
        c.execute("UPDATE todos SET status='completed' WHERE id=?", (todo_id,))
        conn.commit()
        conn.close()

        settings = get_settings()
        api_key = get_server_api_key() or settings.get("server_api_key")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'x-admin-passcode': settings.get('admin_passcode', '579110')
        }
        if api_key:
            headers['X-Meloscribe-Key'] = api_key
        try:
            import urllib.request
            req = urllib.request.Request(f"https://api.meloscribe.dev/api/todos/{todo_id}/complete", headers=headers, method="PATCH")
            urllib.request.urlopen(req, timeout=5.0)
        except Exception:
            pass

        return f"To-Do '{row['song_name']}' wurde erfolgreich als erledigt markiert."

    elif action in ("delete", "remove"):
        if not song_name:
            conn.close()
            return "Fehler: Kein Songname angegeben."
        s = song_name.strip()
        row = c.execute("SELECT id, song_name FROM todos WHERE song_name LIKE ? LIMIT 1", (f"%{s}%",)).fetchone()
        if not row:
            conn.close()
            return f"Song '{s}' wurde in der Todo-Liste nicht gefunden."
            
        todo_id = row["id"]
        c.execute("DELETE FROM todos WHERE id=?", (todo_id,))
        conn.commit()
        conn.close()
        
        settings = get_settings()
        api_key = get_server_api_key() or settings.get("server_api_key")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'x-admin-passcode': settings.get('admin_passcode', '579110')
        }
        if api_key:
            headers['X-Meloscribe-Key'] = api_key
        try:
            import urllib.request
            req = urllib.request.Request(f"https://api.meloscribe.dev/api/todos/{todo_id}", headers=headers, method="DELETE")
            urllib.request.urlopen(req, timeout=5.0)
        except Exception:
            pass

        return f"Song '{row['song_name']}' wurde aus der Todo-Liste entfernt."

    elif action == "priority":
        if not song_name:
            conn.close()
            return "Fehler: Kein Songname angegeben."
        s = song_name.strip()
        p_name = f"[PRIORITY] {s}" if not s.startswith("[PRIORITY]") else s
        c.execute("INSERT INTO todos (song_name, status, added_date) VALUES (?, 'pending', ?)", (p_name, datetime.datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return f"Song '{p_name}' wurde mit höchster Priorität zur To-Do-Liste hinzugefügt."
        
    conn.close()
    return f"Unbekannte Aktion '{action}'. Moeglich sind: list, add, complete, delete, priority."

LEARNINGS_PATH = Path(__file__).parent / "agent_learning.json"

def get_learnings() -> list:
    """Liest alle bisher gelernten Regeln, Anweisungen und Erfahrungen aus agent_learning.json."""
    try:
        if LEARNINGS_PATH.exists():
            with open(LEARNINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("learnings", [])
    except Exception as e:
        print(f"[AI Agent] Failed to read learnings: {e}")
    return []

def record_learning(category: str, lesson: str) -> str:
    """Speichert eine neue Erkenntnis, eine Regel-Korrektur oder Vorliebe von Tobias in das persistente Gedächtnis (agent_learning.json).
    
    Args:
        category: Kategorie (z.B. 'user_preference', 'workflow_rules', 'bot_behavior', 'asset_handling').
        lesson: Die konkrete Regel, Erkenntnis oder Anweisung, die sich der Agent dauerhaft merken MUSS.
    """
    CURRENT_TURN_ACTIONS.append(f"record_learning(category='{category}', lesson='{lesson[:30]}...')")
    try:
        learnings = get_learnings()
        new_entry = {
            "id": len(learnings) + 1,
            "category": category.strip(),
            "lesson": lesson.strip(),
            "timestamp": datetime.datetime.now().isoformat()
        }
        learnings.append(new_entry)
        with open(LEARNINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"learnings": learnings}, f, indent=2, ensure_ascii=False)
        return f"Erkenntnis erfolgreich im Langzeitgedaechtnis (agent_learning.json) gespeichert (ID {new_entry['id']}): '{lesson}'"
    except Exception as e:
        return f"Fehler beim Speichern im Langzeitgedaechtnis: {e}"

def get_latest_uploads(platform: str = "", limit: int = 10) -> str:
    """Ruft die neuesten Video-Uploads ueber alle Social-Media-Plattformen (YouTube, TikTok, Instagram, Facebook, Threads) aus analytics.db ab.
    
    Args:
        platform: Optionaler Plattformfilter ('youtube', 'tiktok', 'instagram', 'facebook', 'threads' oder leer fuer alle).
        limit: Maximale Anzahl der zurueckgegebenen Uploads (Standard: 10).
    """
    CURRENT_TURN_ACTIONS.append(f"get_latest_uploads(platform='{platform}', limit={limit})")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    query = "SELECT song_name, platform, title, publish_date, views, likes, comments, shares, saves, format, url FROM videos "
    params = []
    if platform.strip():
        query += "WHERE LOWER(platform) = ? "
        params.append(platform.strip().lower())
    query += "ORDER BY publish_date DESC LIMIT ?"
    params.append(limit)
    
    rows = [dict(r) for r in c.execute(query, params).fetchall()]
    conn.close()
    
    if not rows:
        return f"Keine Video-Uploads fuer Plattform '{platform}' in der Datenbank gefunden."
        
    lines = [f"Die {len(rows)} neuesten Video-Uploads (chronologisch):"]
    for v in rows:
        p_date = v.get("publish_date", "Unbekannt")
        p_name = v.get("platform", "").upper()
        s_name = v.get("song_name", "Unbekannt")
        v_count = v.get("views", 0)
        l_count = v.get("likes", 0)
        lines.append(f"- [{p_name}] {s_name} ({p_date}) → {v_count:,} Views, {l_count:,} Likes | Format: {v.get('format', 'Tutorial')}")
    return "\n".join(lines)

def get_video_analytics(song_name: str = "") -> str:
    """Analysiert die Performance aller Videos zu einem bestimmten Song ueber alle Plattformen.
    
    Args:
        song_name: Name des Songs (z.B. 'River Flows in You'). Falls leer, wird das allerneueste Video detailliert aufgeschluesselt.
    """
    CURRENT_TURN_ACTIONS.append(f"get_video_analytics(song_name='{song_name}')")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    target_song = song_name.strip()
    if not target_song:
        latest = c.execute("SELECT song_name FROM videos ORDER BY publish_date DESC LIMIT 1").fetchone()
        if not latest:
            conn.close()
            return "Keine Videos in der Datenbank vorhanden."
        target_song = latest["song_name"]
        
    rows = [dict(r) for r in c.execute("SELECT platform, title, publish_date, views, likes, comments, shares, saves, format, reach, avg_view_pct FROM videos WHERE song_name LIKE ? ORDER BY publish_date DESC", (f"%{target_song}%",)).fetchall()]
    conn.close()
    
    if not rows:
        return f"Keine Analytics-Eintraege fuer '{target_song}' gefunden."
        
    total_v = sum(r.get("views") or 0 for r in rows)
    total_l = sum(r.get("likes") or 0 for r in rows)
    total_c = sum(r.get("comments") or 0 for r in rows)
    
    res = [
        f"Performance-Analyse fuer '{target_song}':",
        f"- Gesamt-Views ueber alle Plattformen: {total_v:,}",
        f"- Gesamt-Likes: {total_l:,} | Kommentare: {total_c:,}",
        f"- Aufgeschluesselte Uploads ({len(rows)} Videos):"
    ]
    for r in rows:
        res.append(f"  * [{r.get('platform', '').upper()}] ({r.get('publish_date')}): {r.get('views', 0):,} Views, {r.get('likes', 0):,} Likes | Format: {r.get('format', 'Tutorial')}")
    return "\n".join(res)

def trigger_social_sync(platform: str = "all") -> str:
    """Startet die Live-Synchronisation der Social Media APIs (YouTube, TikTok, Instagram, Facebook, Threads, Pinterest) im Hintergrund, um brandaktuelle Aufrufzahlen abzurufen.
    
    Args:
        platform: 'all', 'youtube', 'tiktok', 'instagram', 'facebook', 'threads', 'pinterest'.
    """
    CURRENT_TURN_ACTIONS.append(f"trigger_social_sync(platform='{platform}')")
    
    def _run_sync():
        try:
            plat = platform.lower()
            if plat in ("all", "youtube"):
                from yt_sync import sync_youtube
                sync_youtube()
            if plat in ("all", "tiktok"):
                from tiktok_sync import sync_tiktok
                sync_tiktok()
            if plat in ("all", "instagram"):
                from ig_sync import sync_instagram
                sync_instagram()
            if plat in ("all", "facebook"):
                from fb_sync import sync_facebook
                sync_facebook()
            if plat in ("all", "threads"):
                from threads_sync import sync_threads
                sync_threads()
            if plat in ("all", "pinterest"):
                from pinterest_sync import sync_pinterest
                sync_pinterest()
            print(f"[Agent Sync] Completed social sync for '{platform}'")
        except Exception as e:
            print(f"[Agent Sync] Error during sync: {e}")
            
    thread = threading.Thread(target=_run_sync, daemon=True)
    thread.start()
    return f"Live-Synchronisation der Social Media APIs fuer '{platform}' wurde im Hintergrund gestartet! Die Zahlen werden aktualisiert."

def check_vm_health() -> str:
    """Prueft live per SSH den Gesamtzustand der Oracle Server VM (152.70.23.171).
    Ermittelt Uptime, System-Auslastung, Festplattenspeicher (/), RAM-Auslastung, Status der Hintergrunddienste (oci-sniper, oci-uploader, nginx), Anzahl der gestageten Songs und die neuesten Uploader-Logzeilen.
    """
    CURRENT_TURN_ACTIONS.append("check_vm_health()")
    if not os.path.exists(SSH_KEY):
        return f"Fehler: SSH Key unter {SSH_KEY} nicht gefunden. Verbindung zur VM nicht moeglich."
        
    remote_script = (
        "echo '=== UPTIME & LOAD ==='; uptime; "
        "echo '=== DISK USAGE ==='; df -h /; "
        "echo '=== MEMORY ==='; free -h; "
        "echo '=== SERVICES ==='; "
        "for s in oci-sniper oci-uploader nginx; do echo \"$s: $(systemctl is-active $s)\"; done; "
        "echo '=== STAGING QUEUE ==='; ls -1 /home/ubuntu/meloscribe/staging 2>/dev/null | wc -l; "
        "echo '=== LATEST UPLOADER LOGS ==='; tail -n 8 /home/ubuntu/meloscribe/uploader.log 2>/dev/null || true"
    )
    cmd = [
        "ssh", "-i", SSH_KEY,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=8",
        f"{SERVER_USER}@{SERVER_IP}",
        remote_script
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if res.returncode != 0 and not res.stdout:
            return f"Fehler bei SSH-Abfrage der VM: {res.stderr.strip()}"
        return f"Oracle VM Status ({SERVER_IP}):\n{res.stdout.strip()}"
    except subprocess.TimeoutExpired:
        return f"Timeout: Die VM ({SERVER_IP}) hat innerhalb von 12 Sekunden nicht geantwortet."
    except Exception as e:
        return f"Fehler beim Pruefen der VM: {e}"

def execute_vm_command(command: str) -> str:
    """Fuehrt einen beliebigen Shell-Befehl per SSH direkt im Terminal auf der Oracle Server VM (152.70.23.171) aus und liefert die Terminal-Ausgabe (stdout/stderr) zurueck.
    
    Args:
        command: Der auszufuehrende Linux-Befehl (z.B. 'tail -n 25 /home/ubuntu/meloscribe/uploader.log', 'ps aux | grep python', 'ls -la /home/ubuntu/meloscribe/staging', 'systemctl status oci-uploader --no-pager').
    """
    CURRENT_TURN_ACTIONS.append(f"execute_vm_command(command='{command[:45]}...')")
    if not os.path.exists(SSH_KEY):
        return f"Fehler: SSH Key unter {SSH_KEY} nicht gefunden."
        
    cmd = [
        "ssh", "-i", SSH_KEY,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=8",
        f"{SERVER_USER}@{SERVER_IP}",
        command
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        out = res.stdout.strip()
        err = res.stderr.strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[STDERR]\n{err}")
        return "\n".join(parts) if parts else "(Befehl erfolgreich ohne Ausgabe ausgefuehrt)"
    except subprocess.TimeoutExpired:
        return f"Timeout: Befehl '{command}' hat nach 25s nicht beendet."
    except Exception as e:
        return f"Fehler bei VM-Terminal-Ausfuehrung: {e}"

def execute_local_terminal_command(command: str) -> str:
    """Fuehrt einen beliebigen Terminal-Befehl lokal auf dem Windows-Host im meloscribe-app Workspace aus.
    
    Args:
        command: Der lokale Shell-Befehl (z.B. 'git status', 'dir', 'tasklist | findstr python').
    """
    CURRENT_TURN_ACTIONS.append(f"execute_local_terminal_command(command='{command[:45]}...')")
    try:
        res = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=20)
        out = res.stdout.strip()
        err = res.stderr.strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[STDERR]\n{err}")
        return "\n".join(parts) if parts else "(Befehl erfolgreich ohne Ausgabe ausgefuehrt)"
    except subprocess.TimeoutExpired:
        return f"Timeout bei lokalem Befehl '{command}'."
    except Exception as e:
        return f"Fehler bei lokalem Terminal-Befehl: {e}"

# Registered tools for Gemini Agent
AGENT_TOOLS = [
    get_community_wishlist,
    manage_community_suggestion,
    get_upload_queue,
    manage_upload_queue,
    inspect_catalog,
    get_recent_sales,
    get_audience_demographics,
    audit_song_assets,
    check_missing_vm_assets,
    upload_missing_assets_to_vm,
    trigger_arrangeme_upload,
    stage_song_to_vm,
    manage_todo,
    record_learning,
    get_learnings,
    get_latest_uploads,
    get_video_analytics,
    trigger_social_sync,
    check_vm_health,
    execute_vm_command,
    execute_local_terminal_command
]

agent_model = genai.GenerativeModel(MODEL_NAME, tools=AGENT_TOOLS)
fallback_agent_model = genai.GenerativeModel(FALLBACK_MODEL_NAME, tools=AGENT_TOOLS)

# -------------------------------------------------------------------
# DAILY BRIEFING GENERATION
# -------------------------------------------------------------------

def generate_daily_briefing():
    """Generate a new daily briefing using trends, wishlist and local analytics with Gemini 3.8 Flash."""
    trends = get_all_trends()
    local_data = fetch_recent_data()
    project_rules = get_project_context()
    learnings = get_learnings()
    
    prompt = f"""
Rolle: Du bist der knallharte Daten-Analyst und AI Agent fuer „meloscribe“. Dein Ziel ist es, Tobias ein exaktes Status-Update zu geben, wie sein Content performt.

Projekt-Kontext:
{project_rules}

Gelerntes Wissen & Richtlinien:
{json.dumps([l.get('lesson') for l in learnings], indent=2, ensure_ascii=False)}

Daten-Grundlage (Tobias's performance data):
{json.dumps(local_data, indent=2, ensure_ascii=False)}

Aktuelle globale Trends (Spotify/Last.fm, YouTube Music, Google Trends):
{json.dumps(trends, indent=2, ensure_ascii=False)}

Deine Aufgabe:
Erstelle ein Status-Update (Briefing) auf DEUTSCH.
- Keine generischen Ratschlaege.
- PFLICHT: Nenne exakt, welche Songs (aus den lokalen Daten!) aktuell am staerksten wachsen oder die meisten Views/Saves haben.
- PFLICHT: Vergleiche die Plattformen (TikTok vs. YouTube vs. Instagram).
- PFLICHT: Berücksichtige die am höchsten gevoteten Community-Wünsche von der Website (aus `community_wishlist`)! Wenn Hörer aktiv nach einem Song fragen, priorisiere ihn in deiner Analyse und deinen 10 Song-Vorschlägen.
- PFLICHT: Nenne 10 GANZ KONKRETE neue Songs aus den globalen Trends und der Community Wishlist, die perfekt zur Zielgruppe (Klavierspieler Pop/Film) passen. Schlage NIEMALS Songs vor, die in `dismissed_suggestions` oder `current_todo_list` stehen!

Output MUST be valid JSON matching this exact structure:
{{
  "recommendation": "Eine kurze, sehr spezifische Status-Zusammenfassung (2-3 Saetze): Welcher alte Song laeuft am besten? Welcher Trend- oder Wishlist-Song MUSS gespielt werden?",
  "analysis": "Detaillierte Analyse (1-2 Absaetze). Verknuepfe harte Zahlen aus den Analytics mit den Trends und Kundenwünschen.",
  "suggested_songs": ["Songname - Autor", "Songname - Autor", ...] // EXAKT 10 SONGS!
}}
WICHTIG: Die suggested_songs MUeSSEN im Format 'Songname - Autor' sein.
Only output JSON. No markdown wrappers.
"""
    try:
        try:
            response = model.generate_content(prompt)
        except Exception as primary_e:
            err_str = str(primary_e)
            if "429" in err_str or "quota" in err_str.lower() or "resource_exhausted" in err_str.lower():
                print(f"[AI Agent] Daily briefing 429 on {MODEL_NAME}, failing over to {FALLBACK_MODEL_NAME}...")
                response = fallback_model.generate_content(prompt)
            else:
                raise primary_e
        raw_text = response.text
        
        text = raw_text.replace("```json", "").replace("```", "").strip()
        json_start = text.find('{')
        json_end = text.rfind('}')
        if json_start != -1 and json_end != -1:
            text = text[json_start:json_end + 1]
        
        data = json.loads(text)
        if "recommendation" not in data or "analysis" not in data:
            return None
        
        if "suggested_songs" not in data:
            data["suggested_songs"] = []
        
        # Save to DB
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO ai_reports (report_date, recommendation_text, analysis_text, suggested_songs) VALUES (?, ?, ?, ?)",
                  (datetime.datetime.now().isoformat(), data["recommendation"], data["analysis"], json.dumps(data["suggested_songs"])))
        conn.commit()
        conn.close()
        
        return data
    except Exception as e:
        print(f"[AI Agent] Error generating daily briefing: {e}")
        raise e

def get_latest_briefing():
    """Get the latest briefing from DB, or generate one if older than 24h."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM ai_reports ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    
    generate_new = False
    if not row:
        generate_new = True
    else:
        report_date = datetime.datetime.fromisoformat(row["report_date"])
        if (datetime.datetime.now() - report_date).total_seconds() > 86400:
            generate_new = True
            
    if generate_new:
        try:
            new_briefing = generate_daily_briefing()
            if new_briefing:
                return new_briefing
        except Exception as e:
            print(f"[AI Agent] Failed to generate new briefing, falling back to cache if available: {e}")
            if not row:
                raise e
    
    return {
        "recommendation": row["recommendation_text"],
        "analysis": row["analysis_text"],
        "suggested_songs": json.loads(row["suggested_songs"])
    }

# -------------------------------------------------------------------
# AGENT CHAT & AUTONOMOUS ACTION ENGINE
# -------------------------------------------------------------------

def chat_with_agent(message: str, history: list, current_tab: str = None):
    """Chat with the agent using history, comprehensive context, learning memory and automatic tool execution."""
    global CURRENT_TURN_ACTIONS
    CURRENT_TURN_ACTIONS = []
    
    formatted_history = []
    for msg in history:
        # Ignore system messages or empty entries
        role = "user" if msg.get("role") == "user" else "model"
        content = msg.get("content", "")
        if content:
            formatted_history.append({"role": role, "parts": [content]})
            
    trends = get_all_trends()
    local_data = fetch_recent_data()
    project_doc = get_project_context()
    catalog = get_catalog()
    learnings = get_learnings()

    tab_descriptions = {
        "master": "Production Pipeline (MasterTab - Workflow, Audio, Synthesia, Stems, Video Crop Preview & Hook Editor, Rendern)",
        "recycling": "Content Recycling (RecyclingTab - 9:16 Re-Cuts, Safe-Crop Jitter, Evergreen Radar, AI Content Brain, Dual-Slider)",
        "insights": "Channel & Revenue Insights (InsightsTab - Channel-Wachstum, Video-Performance, Lifetime Views, Einnahmen)",
        "ai": "AI Advisor (AiAdvisorTab - Strategische Briefings, Empfehlungen, Kanal-Entwicklung, Community Wishlist)",
        "server": "Oracle Server & Staging (ServerTab - Oracle VM Sync, Status, SCP Assets, Upload Queue, Health Checks)",
        "website": "Website & Catalog (WebsiteTab - Notenkatalog, Preise, Downloads, Kundenbestellungen, Wishlist)",
        "settings": "Settings (SettingsTab - API Keys, Pfade, Cakewalk, Soundfonts, Accounts)"
    }
    active_tab_info = tab_descriptions.get(current_tab, f"Tab '{current_tab}'" if current_tab else "Desktop Studio")
    
    system_instruction = f"""
Rolle: Du bist der intelligente AI Operations Agent und Lead-Stratege fuer „meloscribe“.
Du arbeitest direkt mit Tobias zusammen. Du beantwortest nicht nur strategische und analytische Fragen, sondern fuehrst auch operative Aktionen in der meloscribe Desktop-Pipeline selbstaendig aus.

DEIN WISSEN & KONTEXT:
1. Projekt-Architektur & Dateikonventionen (aus meloscribe-app-project.md):
{project_doc}

2. Aktueller Notenkatalog im Shop ({len(catalog)} Songs in songs.json):
{json.dumps([{"title": s.get("title"), "artist": s.get("artist"), "difficulty": s.get("difficulty"), "price": s.get("price", "6.00"), "hasEasy": s.get("hasEasy", False), "arrangeme": bool(s.get("arrangemeLink"))} for s in catalog], ensure_ascii=False)}

3. Live App- & Performance-Daten:
- Lifetime Views: {local_data.get('total_lifetime_views')}
- Neueste Video-Uploads (chronologisch, allerneueste Uploads zuerst!):
{json.dumps(local_data.get('latest_video_uploads'), ensure_ascii=False)}
- Top Songs (letzte 14 Tage): {json.dumps(local_data.get('recent_top_songs'))}
- Aktuelle Todos (Live-Synchronisiert): {json.dumps(local_data.get('current_todo_list'), ensure_ascii=False)}
- Format-Performance: {json.dumps(local_data.get('format_performance'))}
- Channel Insights: {json.dumps(local_data.get('channel_insights'))}
- Revenue/Sales: {json.dumps(local_data.get('revenue_summary'))}
- Verkäufe nach Songs (Echte Zahlungen, keine Tests): {json.dumps(local_data.get('purchases_stats'))}

4. Website Wishlist & Community Song-Wünsche (live von Website-Besuchern auf meloscribe.com gevotet):
{json.dumps(local_data.get('community_wishlist'), indent=2, ensure_ascii=False)}

5. Staging- & Upload-Warteschlange (Server VM queue.db):
{json.dumps(local_data.get('upcoming_upload_queue'), indent=2, ensure_ascii=False)}

6. Globale Musiktrends:
{json.dumps(trends[:15] if isinstance(trends, list) else trends)}

7. DEIN PERSISTENTES GEDAECHTNIS & GELERNTE REGELN (aus agent_learning.json):
{json.dumps(learnings, indent=2, ensure_ascii=False)}

8. AKTUELLER WORKSPACE-KONTEXT DES NUTZERS:
- Tobias befindet sich aktuell in der App im Tab: {active_tab_info} (Key: '{current_tab}').
- WICHTIG: Wenn Tobias Fragen zu UI-Elementen stellt (z.B. "Warum buggt der Button X?", "Was bedeutet dieser Schalter?", "Wo finde ich Regler Y?"), weißt du direkt, dass er sich auf den sichtbaren Tab '{active_tab_info}' bezieht.
- BEACHTE: Wenn seine Frage allgemein ist oder sich auf andere Bereiche bezieht, antworte frei und ohne Einschränkung auf den Tab. Habe den Tab einfach als visuellen Arbeitskontext im Hinterkopf.

KRITISCHE PFLICHT-ANWEISUNG ZUR SELBSTVERBESSERUNG & GEDAECHTNIS-PFLEGE:
- Du besitzt ein persistentes Langzeitgedaechtnis (`agent_learning.json`), das du aktiv pflegen MUSST!
- Wann immer Tobias dir eine Anweisung, Korrektur, Vorliebe oder Regel mitteilt (z.B. "Merke dir...", "Mach das ab jetzt immer so...", "Vergiss nicht...", "Achte darauf..."), oder wenn du bei einem Bot oder einer Datei eine Besonderheit loest:
  Bist du VERPFLICHTET, unverzueglich das Tool `record_learning(category, lesson)` aufzurufen!
- Ignoriere diese Pflege NIEMALS. Du bist ein lernender Agent: Du nutzt dein gelerntes Wissen bei jeder Entscheidung und erweiterst es kontinuierlich.

VERHALTEN:
- Du hast VOLLSTAeNDIGEN ZUGRIFF auf die Datenbank `analytics.db`, die Server-Queue `queue.db`, die Website-Wishlist (`suggestions`), den Shop-Katalog (`songs.json`) sowie saemtliche Social-Media-APIs (YouTube, TikTok, Facebook, Instagram, Threads, Pinterest).
- Wenn Tobias nach der Wishlist, Song-Wünschen der Community, Website-Vorschlägen oder beliebten Hörer-Wünschen fragt:
  Nutze `get_community_wishlist` oder zitiere die Daten aus Punkt 4 ('Website Wishlist')! Nenne exakte Songtitel, Interpreten und Stimmenanzahlen (Votes). Biete ihm aktiv an, beliebte Wishlist-Songs mit `manage_community_suggestion(action='add_to_todo', ...)` direkt als To-Do anzulegen!
- Wenn Tobias nach der Upload-Queue oder geplanten Veröffentlichungen fragt:
  Nutze `get_upload_queue`! Nenne geplante Termine, Songs, Plattformen (YouTube, TikTok, Instagram etc.) und den Status (pending, completed, failed).
- Wenn Tobias nach dem Notenkatalog, Preisen, fehlenden Easy-Versionen oder ArrangeMe-Links fragt:
  Nutze `inspect_catalog`!
- Wenn Tobias nach echten Verkäufen, Bestellungen oder Einnahmen fragt:
  Nutze `get_recent_sales`!
- Wenn Tobias nach Demografie, Ländern oder Zielgruppen fragt:
  Nutze `get_audience_demographics`!
- Wenn Tobias nach neuen Videos fragt (z.B. "wie macht sich unser neuestes video?", "welcher song wurde zuletzt hochgeladen?", "wie laufen die uploads?"):
  Beantworte dies SOFORT und PRAeZISE anhand von 'Neueste Video-Uploads' oder nutze `get_latest_uploads` / `get_video_analytics`! Behaupte NIEMALS, du haettest keine Daten zu Uploads oder einzelnen Videos. Nenne exakte Plattformen, Songnamen, Veroeffentlichungsdaten, Views und Likes!
- Wenn Tobias eine FRAGE stellt (z.B. "Wie laeuft mein Kanal?", "Welche Videos performen am besten?", "Was fuer Songs soll ich aufnehmen?"):
  Antworte direkt, ehrlich, messerscharf und auf DEUTSCH. Nutze deine Kontextdaten. Keine leeren Floskeln. Nenne konkrete Zahlen und Vergleiche.
- Wenn Tobias eine AKTION will:
  Nutze deine TOOLS! Du verfuegst ueber:
  - `get_community_wishlist`: Ruft alle Song-Wünsche von der Website inklusive Votes ab.
  - `manage_community_suggestion`: Fügt Wishlist-Songs zur To-Do-Liste hinzu, markiert sie als completed oder löscht sie.
  - `get_upload_queue`: Ruft die Staging- und Upload-Queue von der VM ab.
  - `manage_upload_queue`: Verwaltet Upload-Tasks (toggle, reschedule, delete).
  - `inspect_catalog`: Analysiert den Notenkatalog auf Preise, fehlende Easy-Versionen und ArrangeMe-Links.
  - `get_recent_sales`: Zeigt echte Sheet-Music-Verkäufe (keine Sandbox-Tests).
  - `get_audience_demographics`: Liefert Hörer-Demografie (Länder, Alter, Geschlecht).
  - `manage_todo`: To-Do-Liste verwalten (add, complete/done, delete, priority, list).
  - `check_vm_health`: Prüft live per SSH den Zustand der VM (Uptime, Disk, RAM, Sniper/Uploader-Dienste, Logs).
  - `execute_vm_command`: Führt beliebige Linux-Befehle per SSH auf der VM aus.
  - `execute_local_terminal_command`: Führt beliebige Terminal-Befehle lokal auf dem Windows-Host aus.
  - `check_missing_vm_assets`: Vergleicht lokale Renderings mit der VM.
  - `upload_missing_assets_to_vm`: Lädt fehlende MP4s per SCP auf die VM.
  - `trigger_arrangeme_upload`: Startet den Playwright-Upload-Bot für ArrangeMe.
  - `audit_song_assets`: Prüft lokale Dateien für einen Song (Normal, Slow, Easy, Cover, PDF).
  - `stage_song_to_vm`: Startet das vollständige Server-Staging.
  - `trigger_social_sync`: Startet Social-Media-API-Sync.
  - `record_learning`: Speichert Regeln/Wünsche im persistenten Gedächtnis.
  - `get_learnings`: Ruft alle gelernten Regeln ab.
- Wenn Tobias fragt: "Funktionierst du wie ein Agent? Kannst du per Terminal die VM checken?":
  Ja! Du bist ein autonomer Software-Agent mit direkter SSH- und Terminal-Execution. Rufe in so einem Fall direkt `check_vm_health` auf und praesentiere ihm die echten, live von der VM abgefragten Server-Daten!
- Nach Ausfuehrung eines Tools bestaetigst du Tobias knapp und professionell auf Deutsch, was getan oder ermittelt wurde.
- Wenn du Songs vorschlaegst, formatiere sie als: * Songname - Autor
"""

    # Try primary model (Gemini 3.8 Flash), failover seamlessly to Gemini 2.5 Flash on 429
    try:
        chat = agent_model.start_chat(history=formatted_history, enable_automatic_function_calling=True)
        prompt_with_context = f"{system_instruction}\n\nTobias: {message}"
        response = chat.send_message(prompt_with_context)
        reply_text = response.text if response and response.text else "Ich habe die Aktion ausgefuehrt."
        return {
            "reply": reply_text,
            "actions": list(CURRENT_TURN_ACTIONS)
        }
    except Exception as primary_err:
        err_str = str(primary_err)
        if "429" in err_str or "quota" in err_str.lower() or "resource_exhausted" in err_str.lower():
            print(f"[AI Agent] 429 rate limit on {MODEL_NAME}. Failing over to {FALLBACK_MODEL_NAME}...")
            try:
                chat_fb = fallback_agent_model.start_chat(history=formatted_history, enable_automatic_function_calling=True)
                prompt_with_context = f"{system_instruction}\n\nTobias: {message}"
                response_fb = chat_fb.send_message(prompt_with_context)
                reply_text = response_fb.text if response_fb and response_fb.text else "Ich habe die Aktion ausgefuehrt."
                return {
                    "reply": reply_text,
                    "actions": list(CURRENT_TURN_ACTIONS)
                }
            except Exception as fb_err:
                print(f"[AI Agent] Fallback model error: {fb_err}")
                return {
                    "reply": "Die Gemini API meldet gerade ein kurzes Ratenlimit (429). Bitte warte einen kurzen Moment.",
                    "actions": list(CURRENT_TURN_ACTIONS)
                }
        else:
            print(f"[AI Agent] Chat error: {primary_err}")
            raise primary_err

if __name__ == "__main__":
    print(f"Using Model: {MODEL_NAME}")
    print("Testing Briefing...")
    b = get_latest_briefing()
    print("Briefing recommendation:", b.get("recommendation")[:120])
