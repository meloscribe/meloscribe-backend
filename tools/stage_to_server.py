import os
import sys
import argparse
import subprocess
import json
import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# Load settings from local backend
sys.path.append(os.path.join(os.path.dirname(__file__), "meloscribe", "backend"))
try:
    from settings import load_settings
    settings = load_settings()
except Exception:
    settings = {}

SSH_KEY = r"C:\Dev\ssh-key-2026-05-07.key"
if not os.path.exists(SSH_KEY):
    SSH_KEY = r"C:\Dev\meloscribe\ssh-key-2026-05-07.key"
SERVER_IP = "152.70.23.171"
SERVER_USER = "ubuntu"
SERVER_DIR = "/home/ubuntu/meloscribe"

def run_ssh_command(cmd_str):
    escaped_cmd = cmd_str.replace('"', '\\"')
    ssh_cmd_str = f'ssh -i "{SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -o IdentitiesOnly=yes {SERVER_USER}@{SERVER_IP} "{escaped_cmd}"'
    res = subprocess.run(ssh_cmd_str, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        print(f"SSH Command failed: {cmd_str}\nError: {res.stderr}")
    return res

def run_scp_command(local_path, remote_path):
    scp_cmd_str = f'scp -i "{SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -o IdentitiesOnly=yes "{local_path}" {SERVER_USER}@{SERVER_IP}:"{remote_path}"'
    res = subprocess.run(scp_cmd_str, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        print(f"SCP Upload failed: {local_path} -> {remote_path}\nError: {res.stderr}")
    return res

def get_base_schedule_time(requested_date, requested_time):
    # Fetch maximum pending schedule_time from server database (excluding pinterest background bulk pins)
    # sqlite3 outputs the result or empty string
    print("PROGRESS: 10% (Checking server queue for auto-scheduling spacing...)")
    sql = "SELECT max(schedule_time) FROM upload_queue WHERE status = 'pending' AND mode != 'pinterest';"
    res = run_ssh_command(f"sqlite3 {SERVER_DIR}/queue.db \"{sql}\"")
    
    max_pending = ""
    if res.returncode == 0 and res.stdout.strip():
        max_pending = res.stdout.strip()
        
    interval_days = int(settings.get("schedule_interval_days", 3))
    req_dt = datetime.datetime.strptime(f"{requested_date} {requested_time}", "%Y-%m-%d %H:%M")
    
    if max_pending and max_pending != "NULL" and max_pending != "":
        try:
            max_dt = datetime.datetime.strptime(max_pending, "%Y-%m-%d %H:%M")
            print(f"Latest pending upload on server is scheduled for: {max_pending}")
            # Base the spaced date on the day of max_dt, but preserve the requested release time of day
            spaced_dt = datetime.datetime.combine((max_dt + datetime.timedelta(days=interval_days)).date(), req_dt.time())
            # Only auto-space if the latest server task is reasonable (within 7 days of requested date)
            if spaced_dt > req_dt:
                if spaced_dt <= req_dt + datetime.timedelta(days=7):
                    print(f"Auto-Scheduling spacing activated: Setting base publication to {spaced_dt.strftime('%Y-%m-%d %H:%M')} (spaced {interval_days} days after last post).")
                    return spaced_dt
                else:
                    print(f"WARNING: Server queue max date '{max_dt}' is too far in future (>7 days from requested {req_dt.strftime('%Y-%m-%d')}). Using selected date {req_dt.strftime('%Y-%m-%d %H:%M')}.")
        except Exception as date_err:
            print(f"Error parsing max pending date '{max_pending}': {date_err}")
            
    print(f"No conflicting future uploads. Scheduling base publication for selected datetime: {req_dt.strftime('%Y-%m-%d %H:%M')}")
    return req_dt

def main():
    parser = argparse.ArgumentParser(description='Stages assets to the Oracle Server')
    parser.add_argument('--song', required=True)
    parser.add_argument('--author', required=True)
    parser.add_argument('--price', default="3.00")
    parser.add_argument('--schedule_date', required=True)
    parser.add_argument('--schedule_time', required=True)
    parser.add_argument('--platforms', required=True, help="comma-separated platforms")
    parser.add_argument('--format', default='full_arrangement', help="Song format: full_arrangement")
    parser.add_argument('--condensed', action='store_true', help="Legacy flag")
    parser.add_argument('--has_easy', action='store_true')
    parser.add_argument('--recycling', action='store_true', help="Content recycling staging mode (single video from Recycling dir)")
    parser.add_argument('--teaser_now', action='store_true', help="Upload teaser immediately, space the remaining versions")
    args = parser.parse_args()
    
    song = args.song
    author = args.author
    price = args.price
    platforms = [p.strip().lower() for p in args.platforms.split(",") if p.strip()]
    
    # Re-evaluate settings live at execution time to respect user toggle changes up to the last second
    fresh_settings = load_settings()
    global settings
    settings = fresh_settings

    if not getattr(args, 'recycling', False):
        active_platforms = []
        for p in platforms:
            toggle_key = f"do{p.capitalize()}"
            if fresh_settings.get(toggle_key, True):
                active_platforms.append(p)
            else:
                print(f"[StageToServer] Toggle '{toggle_key}' is OFF in live settings — skipping platform: {p}")
        platforms = active_platforms
    else:
        print(f"[StageToServer] Content Recycling Mode: Using explicitly selected platforms ({', '.join(platforms)})")
            
    if not platforms:
        print("[StageToServer] All target social platforms are turned OFF — skipping server staging.")
        sys.exit(0)
    
    print(f"Starting server-side staging for: {song} (Active platforms: {', '.join(platforms)})")
    
    if not os.path.exists(SSH_KEY):
        print(f"CRITICAL ERROR: SSH Key not found at {SSH_KEY}")
        sys.exit(1)
        
    # 1. Determine scheduling date
    base_dt = get_base_schedule_time(args.schedule_date, args.schedule_time)
    
    # Spacing and Teaser check
    interval_days = int(settings.get("schedule_interval_days", 3))
    
    tiktoks_dir = Path(settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks"))
    covers_dir = Path(settings.get("covers_dir", r"C:\Dev\meloscribe\Covers"))
    packages_dir = Path(settings.get("packages_dir", r"C:\Dev\meloscribe\packages"))
    
    # Auto-create local directories to prevent FileNotFoundError
    os.makedirs(tiktoks_dir, exist_ok=True)
    os.makedirs(covers_dir, exist_ok=True)
    os.makedirs(packages_dir, exist_ok=True)
    
    teaser_exists = False
    for file in os.listdir(tiktoks_dir):
        if file.lower() == f"{song.lower()} teaser.mp4":
            teaser_exists = True
            break
                
    is_viral_part = False
        
    platform_map = {
        "youtube": "yt",
        "instagram": "ig",
        "facebook": "fb",
        "tiktok": "tt",
        "threads": "threads",
        "pinterest": "pinterest"
    }

    def is_hook_active():
        if not teaser_exists:
            return False
        for p in platforms:
            if p == "kofi":
                continue
            p_pref = platform_map.get(p)
            if not p_pref:
                continue
            h_val = settings.get(f"{p_pref}_upload_hook")
            if bool(h_val) if h_val is not None else True:
                return True
        return False

    def is_combination_active(is_easy, profile):
        if is_easy and not args.has_easy:
            return False
        if "kofi" in platforms and profile == "normal":
            return True
        for p in platforms:
            if p == "kofi":
                continue
            p_pref = platform_map.get(p)
            if not p_pref:
                continue
            easy_val = settings.get(f"{p_pref}_upload_easy")
            easy_on = bool(easy_val) if easy_val is not None else True
            
            if is_easy and not easy_on:
                continue

            p_val = settings.get(f"{p_pref}_upload_normal") if profile == "normal" else settings.get(f"{p_pref}_upload_tutorial")
            profile_on = bool(p_val) if p_val is not None else True
            if profile_on:
                return True
        return False

    dates = {}
    current_dt = base_dt
    
    if is_hook_active():
        if getattr(args, 'teaser_now', False) or settings.get("upload_teaser_now", False):
            try:
                now_berlin = datetime.datetime.now(ZoneInfo("Europe/Berlin"))
            except Exception:
                now_berlin = datetime.datetime.now()
            dates["teaser"] = (now_berlin - datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
            now_naive = now_berlin.replace(tzinfo=None) if hasattr(now_berlin, 'tzinfo') and now_berlin.tzinfo else now_berlin
            ref_dt = base_dt if base_dt > now_naive else now_naive
            current_dt = datetime.datetime.combine((ref_dt + datetime.timedelta(days=interval_days)).date(), base_dt.time())
        else:
            dates["teaser"] = current_dt.strftime("%Y-%m-%d %H:%M")
            current_dt += datetime.timedelta(days=interval_days)
        
    # 1. Original Normal
    if is_combination_active(is_easy=False, profile="normal"):
        dates["original_normal"] = current_dt.strftime("%Y-%m-%d %H:%M")
        dates["original_kofi"] = (current_dt + datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
        current_dt += datetime.timedelta(days=interval_days)
        
    # 2. Original Tutorial
    if is_combination_active(is_easy=False, profile="tutorial"):
        dates["original_tutorial"] = current_dt.strftime("%Y-%m-%d %H:%M")
        current_dt += datetime.timedelta(days=interval_days)
        
    # 3. Easy Normal
    if is_combination_active(is_easy=True, profile="normal"):
        dates["easy_normal"] = current_dt.strftime("%Y-%m-%d %H:%M")
        dates["easy_kofi"] = (current_dt + datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
        current_dt += datetime.timedelta(days=interval_days)
        
    # 4. Easy Tutorial
    if is_combination_active(is_easy=True, profile="tutorial"):
        dates["easy_tutorial"] = current_dt.strftime("%Y-%m-%d %H:%M")
        current_dt += datetime.timedelta(days=interval_days)
        
    print(f"[Scheduling Timeline]")
    if "teaser" in dates:
        print(f"  V1 Teaser: {dates['teaser']}")
    if "original_normal" in dates:
        print(f"  V2 Original Normal: {dates['original_normal']} (Ko-Fi: {dates['original_kofi']})")
    if "original_tutorial" in dates:
        print(f"  V3 Original Tutorial: {dates['original_tutorial']}")
    if args.has_easy and "easy_normal" in dates:
        print(f"  V4 Easy Normal: {dates['easy_normal']} (Ko-Fi: {dates['easy_kofi']})")
    if args.has_easy and "easy_tutorial" in dates:
        print(f"  V5 Easy Tutorial: {dates['easy_tutorial']}")
    
    # 2. Sync credential files to the server backend
    print("PROGRESS: 30% (Syncing credential files and configurations to server...)")
    backend_dir = Path(__file__).resolve().parent / "meloscribe" / "backend"
    credentials = [
        "yt_tokens.json",
        "ig_tokens.json",
        "tiktok_tokens.json",
        "threads_tokens.json",
        "pinterest_tokens.json",
        "kofi_cookie.txt",
        "settings.json"
    ]
    
    for cred in credentials:
        cred_path = backend_dir / cred
        if cred_path.exists():
            print(f"Copying {cred} to server...")
            run_scp_command(str(cred_path), f"{SERVER_DIR}/tools/meloscribe/backend/{cred}")
            
    # Also copy upload_bot.py to keep it up to date on server
    upload_bot_local = Path(__file__).resolve().parent / "upload_bot.py"
    if upload_bot_local.exists():
        print("Copying upload_bot.py to server...")
        run_scp_command(str(upload_bot_local), f"{SERVER_DIR}/tools/upload_bot.py")
        
    posters = ["yt_poster.py", "ig_poster.py", "fb_poster.py", "threads_poster.py", "tiktok_poster.py", "pinterest_poster.py"]
    for poster in posters:
        poster_path = backend_dir / poster
        if poster_path.exists():
            print(f"Copying {poster} to server...")
            run_scp_command(str(poster_path), f"{SERVER_DIR}/tools/meloscribe/backend/{poster}")
            
    # 3. Create directories on server
    print("PROGRESS: 50% (Creating staging directories on server...)")
    is_recycling = getattr(args, 'recycling', False)
    staging_song = song if ("recycled" in song.lower()) else (f"{song} Recycled" if is_recycling else song)

    if is_recycling:
        song_dir = f"{SERVER_DIR}/staging/{staging_song}"
        mkdir_cmd = f"mkdir -p \"{song_dir}/TikToks\" \"{song_dir}/Covers\""
    else:
        song_dir = f"{SERVER_DIR}/staging/{song}"
        tiktoks_path = f'"{song_dir}/TikToks"'
        covers_path = f'"{song_dir}/Covers"'
        packages_path = f'"{song_dir}/packages"'
        mkdir_cmd = f"mkdir -p {tiktoks_path} {covers_path} {packages_path}"
    run_ssh_command(mkdir_cmd)
    
    # 4. Copy generated assets
    print("PROGRESS: 70% (Scanning and preparing assets for upload...)")
    upload_tasks = []
    
    if is_recycling:
        recycling_dir = Path(settings.get("recycling_dir", r"C:\Dev\meloscribe\Recycling"))
        local_rec_vid = recycling_dir / f"{song}.mp4"
        if not local_rec_vid.exists():
            local_rec_vid = recycling_dir / f"{staging_song}.mp4"
            
        if local_rec_vid.exists():
            upload_tasks.append((
                str(local_rec_vid),
                f"{SERVER_DIR}/staging/{staging_song}/TikToks/{staging_song}.mp4",
                f"Recycled Video {staging_song}.mp4"
            ))
        else:
            print(f"ERROR: Recycled video not found at {local_rec_vid}")
            sys.exit(1)
            
        # Cover (fallback to base song cover if numbered recycled cover doesn't exist)
        local_cover = covers_dir / f"{song}.jpg"
        if not local_cover.exists():
            base_song_for_cover = re.sub(r'(?i)\s+recycled(?:\s+\d+)?$', '', song).strip()
            if (covers_dir / f"{base_song_for_cover}.jpg").exists():
                local_cover = covers_dir / f"{base_song_for_cover}.jpg"
        if not local_cover.exists():
            print(f"Cover for '{song}' not found in Covers/ — generating via cover_generator.py...")
            subprocess.run([sys.executable, str(Path(__file__).parent / "cover_generator.py"), "--song", song, "--author", author, "--theme", "warm", "--vertical"])
        if local_cover.exists():
            upload_tasks.append((
                str(local_cover),
                f"{SERVER_DIR}/staging/{staging_song}/Covers/{staging_song}.jpg",
                f"Cover {staging_song}.jpg"
            ))
        else:
            print(f"Warning: Cover {local_cover} could not be found or generated.")
    else:
        # Standard video assets
        if tiktoks_dir.exists():
            for file in os.listdir(tiktoks_dir):
                if "easy teaser" in file.lower():
                    continue
                if file.lower().startswith(song.lower()) and file.lower().endswith(".mp4"):
                    upload_tasks.append((
                        str(tiktoks_dir / file),
                        f"{SERVER_DIR}/staging/{song}/TikToks/{file}",
                        f"Video {file}"
                    ))
                
        # Standard cover assets
        if covers_dir.exists():
            for file in os.listdir(covers_dir):
                if file.lower().startswith(song.lower()) and file.lower().endswith(".jpg"):
                    upload_tasks.append((
                        str(covers_dir / file),
                        f"{SERVER_DIR}/staging/{song}/Covers/{file}",
                        f"Cover {file}"
                    ))

        
    total_tasks = len(upload_tasks)
    if total_tasks > 0:
        remote_sizes = {}
        target_song_dir = staging_song if is_recycling else song
        check_cmd = f"find {SERVER_DIR}/staging/{target_song_dir}/TikToks {SERVER_DIR}/staging/{target_song_dir}/Covers -type f -printf '%s %p\\n' 2>/dev/null"
        stat_res = run_ssh_command(check_cmd)
        if stat_res.returncode == 0 and stat_res.stdout:
            for line in stat_res.stdout.strip().splitlines():
                parts = line.strip().split(" ", 1)
                if len(parts) == 2 and parts[0].isdigit():
                    remote_sizes[parts[1]] = int(parts[0])

        for idx, (local, remote, desc) in enumerate(upload_tasks):
            pct = 70 + int((idx / total_tasks) * 20)
            local_sz = os.path.getsize(local) if os.path.exists(local) else 0
            if remote in remote_sizes and remote_sizes[remote] == local_sz:
                print(f"PROGRESS: {pct}% (Already on server: {desc} [{idx+1}/{total_tasks}], skipping)")
                continue
            print(f"PROGRESS: {pct}% (Uploading {desc} [{idx+1}/{total_tasks}]...)")
            run_scp_command(local, remote)
    else:
        print("No assets found to upload.")
        
    # 5. Insert queue tasks on server
    print("PROGRESS: 90% (Registering scheduled jobs in server database...)")
    
    # Clean/create db table just in case
    init_db_sql = """
    CREATE TABLE IF NOT EXISTS upload_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song TEXT,
        author TEXT,
        price TEXT,
        mode TEXT,
        profile TEXT,
        schedule_time TEXT,
        status TEXT DEFAULT 'pending',
        error TEXT,
        attempts INTEGER DEFAULT 0,
        youtube_url TEXT,
        condensed INTEGER DEFAULT 0,
        format TEXT DEFAULT 'full_arrangement'
    );
    """
    # Replace newlines and extra spaces to make it a safe one-liner for ssh/sqlite3
    init_db_sql_clean = " ".join(init_db_sql.strip().split())
    run_ssh_command(f"sqlite3 {SERVER_DIR}/queue.db \"{init_db_sql_clean}\"")
    
    # Clean up existing pending tasks for this song to prevent duplicates stacking up
    if is_recycling:
        print(f"Cleaning up existing pending tasks for '{staging_song}' in queue database...")
        esc_staging_clean = staging_song.replace("'", "''")
        del_sql = f"DELETE FROM upload_queue WHERE song = '{esc_staging_clean}' AND status = 'pending';"
        run_ssh_command(f"sqlite3 {SERVER_DIR}/queue.db \"{del_sql}\"")
    else:
        print(f"Cleaning up existing pending tasks for '{song}' in queue database...")
        esc_song_clean = song.replace("'", "''")
        del_sql = f"DELETE FROM upload_queue WHERE (song = '{esc_song_clean}' OR song = '{esc_song_clean} Easy' OR song = '{esc_song_clean} Teaser' OR song = '{esc_song_clean} Full Package' OR song = '{esc_song_clean} Easy Full Package') AND status = 'pending';"
        run_ssh_command(f"sqlite3 {SERVER_DIR}/queue.db \"{del_sql}\"")
    
    def register_task(song_name, author, price, mode, profile, schedule_time, condensed_val=0, format_val='full_arrangement'):
        # For Facebook, uploads must always be scheduled +4 hours later (e.g. 16:00 -> 20:00 CEST)
        if mode == "facebook":
            try:
                fb_dt = datetime.datetime.strptime(schedule_time, "%Y-%m-%d %H:%M") + datetime.timedelta(hours=4)
                schedule_time = fb_dt.strftime("%Y-%m-%d %H:%M")
            except Exception as e:
                print(f"Warning: Could not adjust Facebook schedule_time '{schedule_time}' by +4h: {e}")

        # Escape single quotes for SQLite
        esc_song = song_name.replace("'", "''")
        esc_author = author.replace("'", "''")
        esc_price = price.replace("'", "''")
        esc_mode = mode.replace("'", "''")
        esc_profile = profile.replace("'", "''")
        esc_time = schedule_time.replace("'", "''")

        # Check if duplicate exists
        check_sql = f"SELECT id FROM upload_queue WHERE song = '{esc_song}' AND mode = '{esc_mode}' AND profile = '{esc_profile}' AND schedule_time = '{esc_time}';"
        check_res = run_ssh_command(f"sqlite3 {SERVER_DIR}/queue.db \"{check_sql}\"")
        if check_res.returncode == 0 and check_res.stdout.strip():
            print(f"  -> Task already exists: {song_name} on {mode} ({profile}) at {schedule_time}. Skipping duplicate.")
            return True
            
        if mode == "kofi":
            sql = f"INSERT INTO upload_queue (song, author, price, mode, profile, schedule_time, status, condensed, format) VALUES ('{esc_song}', '{esc_author}', '{esc_price}', '{esc_mode}', '{esc_profile}', '{esc_time}', 'pending', {condensed_val}, '{format_val}');"
        else:
            sql = f"INSERT INTO upload_queue (song, author, price, mode, profile, schedule_time, status) VALUES ('{esc_song}', '{esc_author}', '{esc_price}', '{esc_mode}', '{esc_profile}', '{esc_time}', 'pending');"
            
        res = run_ssh_command(f"sqlite3 {SERVER_DIR}/queue.db \"{sql}\"")
        if res.returncode != 0:
            print(f"Error registering task {song_name} on {mode}: {res.stderr or res.stdout}")
            return False
        else:
            print(f"  -> Registered: {song_name} on {mode} ({profile}) at {schedule_time}")
            return True

    if is_recycling:
        target_dt = f"{args.schedule_date} {args.schedule_time}"
        print(f"[Content Recycling] Registering single tasks for '{staging_song}' at {target_dt} across: {', '.join(platforms)}")
        for platform in platforms:
            register_task(staging_song, author, price, platform, 'normal', target_dt)
        print("PROGRESS: 100% (Content Recycling Staging completed!)")
        print(f"SUCCESS: Staged recycled '{song}' as '{staging_song}' successfully on Oracle Server!")
        sys.exit(0)

    for platform in platforms:
        if platform == "kofi":
            # Original Ko-Fi
            condensed_val = 1 if is_viral_part else 0
            format_val = 'viral_part' if is_viral_part else 'full_arrangement'
            if "original_kofi" in dates:
                register_task(song, author, price, 'kofi', 'normal', dates['original_kofi'], condensed_val, format_val)
                
            # Easy Ko-Fi
            if args.has_easy and "easy_kofi" in dates:
                register_task(f"{song} Easy", author, price, 'kofi', 'normal', dates['easy_kofi'], condensed_val, format_val)
        else:
            p_pref = platform_map.get(platform)
            if not p_pref:
                continue
            
            easy_v = settings.get(f"{p_pref}_upload_easy")
            upload_easy = bool(easy_v) if easy_v is not None else True
            
            norm_v = settings.get(f"{p_pref}_upload_normal")
            upload_normal = bool(norm_v) if norm_v is not None else True
            
            tut_v = settings.get(f"{p_pref}_upload_tutorial")
            upload_tutorial = bool(tut_v) if tut_v is not None else True



            hook_v = settings.get(f"{p_pref}_upload_hook")
            upload_hook = bool(hook_v) if hook_v is not None else True

            # 1. Teaser / Hook (if exists and enabled for this platform)
            if teaser_exists and "teaser" in dates:
                if upload_hook:
                    register_task(f"{song} Teaser", author, price, platform, 'normal', dates['teaser'])
                else:
                    print(f"  -> Skipping Teaser for {platform} (hook upload disabled)")
                    
            # 2. Original Normal
            if "original_normal" in dates:
                if upload_normal:
                    register_task(song, author, price, platform, 'normal', dates['original_normal'])
                else:
                    print(f"  -> Skipping Original Normal for {platform} (normal upload disabled)")
                
            # 3. Original Tutorial
            if "original_tutorial" in dates:
                if upload_tutorial:
                    register_task(song, author, price, platform, 'tutorial', dates['original_tutorial'])
                else:
                    print(f"  -> Skipping Original Tutorial for {platform} (tutorial upload disabled)")
                
            # 4. Easy Normal
            if args.has_easy and "easy_normal" in dates:
                if upload_easy and upload_normal:
                    register_task(f"{song} Easy", author, price, platform, 'normal', dates['easy_normal'])
                else:
                    print(f"  -> Skipping Easy Normal for {platform} (easy or normal upload disabled)")
                
            # 5. Easy Tutorial
            if args.has_easy and "easy_tutorial" in dates:
                if upload_easy and upload_tutorial:
                    register_task(f"{song} Easy", author, price, platform, 'tutorial', dates['easy_tutorial'])
                else:
                    print(f"  -> Skipping Easy Tutorial for {platform} (easy or tutorial upload disabled)")
                    
    print("PROGRESS: 100% (Staging completed!)")
    print(f"SUCCESS: Staged '{song}' successfully on Oracle Server!")

if __name__ == "__main__":
    main()
