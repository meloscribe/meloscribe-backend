import os
import sys
import time
import sqlite3
import subprocess
import shutil
import datetime

DB_PATH = "/home/ubuntu/meloscribe/queue.db"
STAGING_DIR = "/home/ubuntu/meloscribe/staging"
TOOLS_DIR = "/home/ubuntu/meloscribe/tools"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
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
        )
    """)
    conn.commit()
    # Migration helper for existing DBs
    try:
        cursor.execute("ALTER TABLE upload_queue ADD COLUMN condensed INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE upload_queue ADD COLUMN format TEXT DEFAULT 'full_arrangement'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()

def check_disk_space():
    """Checks free disk space and deletes video files of completed songs if space is below 10GB or 10% free."""
    try:
        total, used, free = shutil.disk_usage("/home/ubuntu")
        free_gb = free / (1024**3)
        free_pct = free / total
        
        print(f"[Cleanup] Disk Space check: {free_gb:.2f} GB free ({free_pct*100:.1f}%)")
        
        if free_gb < 10.0 or free_pct < 0.10:
            print("[Cleanup] Free space is low! Initiating clean up...")
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Find songs where all scheduled upload tasks are completed
            cursor.execute("""
                SELECT DISTINCT song FROM upload_queue 
                WHERE song NOT IN (
                    SELECT DISTINCT song FROM upload_queue WHERE status != 'completed'
                )
            """)
            completed_songs = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            cleaned_any = False
            for song in completed_songs:
                song_stage_path = os.path.join(STAGING_DIR, song)
                if os.path.exists(song_stage_path):
                    # Delete videos inside the song's staging path to save space
                    print(f"[Cleanup] Removing staged files for completed song: {song}")
                    try:
                        shutil.rmtree(song_stage_path)
                        cleaned_any = True
                    except Exception as clean_err:
                        print(f"[Cleanup] Error deleting {song_stage_path}: {clean_err}")
            
            if not cleaned_any:
                print("[Cleanup] Low space but no completed songs found to clean up.")
    except Exception as e:
        print(f"[Cleanup] Error during disk check/cleanup: {e}")

def run_uploads():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Query pending tasks that are due
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("""
        SELECT id, song, author, price, mode, profile, schedule_time, youtube_url, condensed, format 
        FROM upload_queue 
        WHERE status = 'pending' AND datetime(schedule_time) <= datetime(?)
    """, (now_str,))
    
    pending_tasks = cursor.fetchall()
    
    for task in pending_tasks:
        task_id, song, author, price, mode, profile, schedule_time, youtube_url, condensed, format = task
        print(f"[Uploader] Starting task {task_id}: {mode} ({profile}) for '{song}'...")
        
        # Update status to processing
        cursor.execute("UPDATE upload_queue SET status = 'processing' WHERE id = ?", (task_id,))
        conn.commit()
        
        # If this is Ko-Fi, we might need a YouTube URL from the db if it was not passed in the request
        if mode == "kofi" and not youtube_url:
            cursor.execute("""
                SELECT youtube_url FROM upload_queue 
                WHERE song = ? AND mode = 'youtube' AND youtube_url IS NOT NULL AND youtube_url != ''
                LIMIT 1
            """, (song,))
            row = cursor.fetchone()
            if row:
                youtube_url = row[0]
                print(f"[Uploader] Found YouTube URL in DB: {youtube_url}")
        
        # Construct the execution command
        # upload_bot.py is in TOOLS_DIR
        python_bin = "python3"
        bot_path = os.path.join(TOOLS_DIR, "upload_bot.py")
        
        cmd = [
            python_bin, "-u", bot_path,
            "--song", song,
            "--author", author,
            "--mode", mode,
            "--profile", profile
        ]
        
        if mode == "kofi":
            cmd.extend(["--price", price])
            if youtube_url:
                cmd.extend(["--youtube_url", youtube_url])
            if format:
                cmd.extend(["--format", format])
            elif condensed:
                cmd.extend(["--format", "viral_part"])
            else:
                cmd.extend(["--format", "full_arrangement"])
        
        print(f"[Uploader] Command: {' '.join(cmd)}")
        
        # Run upload_bot.py as a subprocess
        try:
            # We direct output to stdout to log it in systemd journal
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=TOOLS_DIR)
            print(res.stdout)
            if res.stderr:
                print("[Uploader] ERRORS:\n", res.stderr)
                
            if res.returncode == 0:
                print(f"[Uploader] SUCCESS: Task {task_id} completed.")
                
                # Check if we generated a YouTube URL and save it in queue
                # upload_bot.py prints "SUCCESS! Video uploaded at https://youtu.be/..."
                yt_url = ""
                for line in res.stdout.split("\n"):
                    if "SUCCESS! Video uploaded at https://youtu.be/" in line:
                        yt_url = line.split("at ")[-1].strip()
                        break
                
                if yt_url:
                    cursor.execute("UPDATE upload_queue SET status = 'completed', youtube_url = ? WHERE id = ?", (yt_url, task_id))
                    # Also update other pending tasks of the same song (like Ko-Fi)
                    cursor.execute("UPDATE upload_queue SET youtube_url = ? WHERE song = ? AND status = 'pending'", (yt_url, song))
                else:
                    cursor.execute("UPDATE upload_queue SET status = 'completed' WHERE id = ?", (task_id,))
                
                conn.commit()
            else:
                # Execution failed
                error_msg = res.stderr or res.stdout or f"Exit code {res.returncode}"
                print(f"[Uploader] FAILED: Task {task_id} failed: {error_msg}")
                cursor.execute("""
                    UPDATE upload_queue 
                    SET status = 'failed', error = ?, attempts = attempts + 1 
                    WHERE id = ?
                """, (error_msg, task_id))
                conn.commit()
                
        except Exception as exec_err:
            error_msg = str(exec_err)
            print(f"[Uploader] FAILED to execute command for task {task_id}: {error_msg}")
            cursor.execute("""
                UPDATE upload_queue 
                SET status = 'failed', error = ?, attempts = attempts + 1 
                WHERE id = ?
            """, (error_msg, task_id))
            conn.commit()
            
    conn.close()

def main():
    print("[Uploader] Server scheduled uploader daemon starting...")
    init_db()
    
    # Run once at startup
    run_uploads()
    check_disk_space()
    
    while True:
        try:
            run_uploads()
            # Disk space check every hour (60 loops of 60s)
            for _ in range(60):
                time.sleep(60)
                run_uploads()
            check_disk_space()
        except KeyboardInterrupt:
            print("[Uploader] Exiting on keyboard interrupt.")
            break
        except Exception as loop_err:
            print(f"[Uploader] Daemon loop error: {loop_err}")
            time.sleep(10)

if __name__ == "__main__":
    main()
