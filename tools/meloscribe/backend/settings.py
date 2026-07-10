import json
from pathlib import Path
import os

SETTINGS_FILE = Path(__file__).parent / "settings.json"

DEFAULT_SETTINGS = {
    "musescore_dir": r"C:\Dev\meloscribe\Scores",
    "musescore_exe": r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    "cakewalk_dir": r"C:\Cakewalk Projects",
    "keysight_dir": r"C:\Dev\meloscribe\Keysight export",
    "tiktok_dir": r"C:\Dev\meloscribe\TikToks",
    "covers_dir": r"C:\Dev\meloscribe\Covers",
    "packages_dir": r"C:\Dev\meloscribe\packages",
    "keysight_exe": r"C:\Program Files (x86)\Steam\steamapps\common\Keysight\Keysight\Binaries\Win64\Keysight-Win64-Shipping.exe",
    "browser_exec": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    "browser_user_data": os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\User Data"),
    "ntfy_topic": "",
    "gemini_api_key": "",
    "ig_app_id": "26975285422066567",
    "ig_app_secret": "",
    "tiktok_client_key": "sbawllqdpf3yk6g8kh",
    "tiktok_client_secret": "",
    "threads_app_id": "26975285422066567",
    "threads_app_secret": "",
    "schedule_interval_days": 3,
    "localUpload": False,
    "desc_template_youtube": (
        "🎹 {song}{label} - {author}\n\n"
        "Enjoy this piano arrangement! Whether you're here to listen or want to learn this piece yourself - I've got you covered.\n\n"
        "Sheet Music (PDF) & free Videos -> Link in Bio\n\n"
        "Check out my channel for more aesthetic piano covers and tutorials!\n\n"
        "#piano #pianocover #pianotutorial #music #synthesia #keysight"
    ),
    "desc_template_instagram": (
        "🎹 {song}{label} - {author}\n\n"
        "Enjoy this piano arrangement! Whether you're here to listen or want to learn this piece yourself - I've got you covered.\n\n"
        "Sheet Music (PDF) & free Videos -> Link in Bio\n\n"
        "Check out my profile for more aesthetic piano covers and tutorials!\n\n"
        "#piano #pianocover #pianotutorial #synthesia #music #pianomusic"
    ),
    "desc_template_facebook": (
        "🎹 {song}{label} - {author}\n\n"
        "Enjoy this piano arrangement! Whether you're here to listen or want to learn this piece yourself - I've got you covered.\n\n"
        "Sheet Music (PDF) & free Videos -> Link in Bio\n\n"
        "Check out my page for more aesthetic piano covers and tutorials!\n\n"
        "#piano #pianocover #synthesia #music"
    ),
    "desc_template_threads": (
        "🎹 {song}{label} - {author}\n\n"
        "Enjoy this piano arrangement! Whether you're here to listen or want to learn this piece yourself - I've got you covered.\n\n"
        "Sheet Music (PDF) & free Videos -> Link in Bio\n\n"
        "Check out my profile for more aesthetic piano covers and tutorials!\n\n"
        "#piano #pianocover #pianotutorial #synthesia #music"
    ),
    "desc_template_tiktok": (
        "🎹 {song}{label} - {author}\n\n"
        "Enjoy this piano arrangement! Whether you're here to listen or want to learn this piece yourself - I've got you covered.\n\n"
        "Sheet Music (PDF) & free Videos -> Link in Bio\n\n"
        "Check out my profile for more aesthetic piano covers and tutorials!\n\n"
        "#piano #pianocover #pianotutorial #music #synthesia #cover"
    ),
    "desc_template_kofi": (
        "Get the learning package for my '{song}' tutorial! This download includes:\n\n"
        "PDF sheet music, a high-quality MIDI file in original speed, a bonus slow-speed MIDI file for easy practice, "
        "and both 2K-quality tutorial videos (Original Version, Slow Version) for offline learning "
        "all packed into a ZIP, because sometimes files get missing after uploading.\n\n"
        "This sheet music/MIDI contains all unique musical sections (Intro, Verse, Chorus, Bridge) as shown in the video. "
        "Repetitions may be omitted for brevity, but all distinct parts are included."
    ),
    "desc_template_kofi_original_full_arrangement": (
        "Get the learning package for my '{song}' tutorial! This download includes:\n\n"
        "PDF sheet music, a high-quality MIDI file in original speed, a bonus slow-speed MIDI file for easy practice, "
        "and both 2K-quality tutorial videos (Original Version, Slow Version) for offline learning "
        "all packed into a ZIP, because sometimes files get missing after uploading.\n\n"
        "This sheet music/MIDI contains all unique musical sections (Intro, Verse, Chorus, Bridge) as shown in the video. "
        "Repetitions may be omitted for brevity, but all distinct parts are included."
    ),
    "desc_template_kofi_original_viral_part": (
        "Get the condensed learning package for my '{song}' tutorial! This download contains the viral/short part of the song and includes:\n\n"
        "PDF sheet music, a high-quality MIDI file in original speed, a bonus slow-speed MIDI file for easy practice, "
        "and both 2K-quality tutorial videos (Original Version, Slow Version) for offline learning "
        "all packed into a ZIP, because sometimes files get missing after uploading.\n\n"
        "This sheet music/MIDI contains all unique musical sections as shown in the video."
    ),
    "desc_template_kofi_easy_full_arrangement": (
        "Get the easy learning package for my '{song}' tutorial! This download includes:\n\n"
        "PDF easy sheet music, a high-quality simplified MIDI file, a bonus slow-speed simplified MIDI file for easy practice, "
        "and both 2K-quality easy tutorial videos (Easy Version, Slow Easy Version) for offline learning "
        "all packed into a ZIP, because sometimes files get missing after uploading.\n\n"
        "This simplified sheet music/MIDI contains all unique musical sections (Intro, Verse, Chorus, Bridge) as shown in the video. "
        "Repetitions may be omitted for brevity, but all distinct parts are included."
    ),
    "desc_template_kofi_easy_viral_part": (
        "Get the condensed easy learning package for my '{song}' tutorial! This download contains the viral/short part of the song in an easy/simplified arrangement and includes:\n\n"
        "PDF easy sheet music, a high-quality simplified MIDI file, a bonus slow-speed simplified MIDI file for easy practice, "
        "and both 2K-quality easy tutorial videos (Easy Version, Slow Easy Version) for offline learning "
        "all packed into a ZIP, because sometimes files get missing after uploading.\n\n"
        "This simplified sheet music/MIDI contains all unique musical sections as shown in the video."
    ),
    "yt_upload_easy": True,
    "ig_upload_easy": True,
    "fb_upload_easy": True,
    "tt_upload_easy": True,
    "threads_upload_easy": True,
    "pinterest_upload_easy": True,
    "pinterest_upload_normal": True,
    "pinterest_upload_tutorial": True,
    "doPinterest": True,
    "desc_template_pinterest": (
        "Enjoy this piano arrangement of {song} by {author}! Whether you're here to listen or want to learn this piece yourself - I've got you covered.\n\n"
        "👉 Click the Pin to get the Sheet Music (PDF), MIDI & practice videos!\n\n"
        "Follow for more aesthetic piano covers and tutorials.\n\n"
        "#piano #pianocover #pianotutorial #sheetmusic #{song} {song_link}"
    )
}

def load_settings():
    if not SETTINGS_FILE.exists():
        save_settings(DEFAULT_SETTINGS)
        settings = DEFAULT_SETTINGS
    else:
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                settings = DEFAULT_SETTINGS.copy()
                settings.update(data)
                
                # Self-healing check to write missing Pinterest template to disk
                if "desc_template_pinterest" not in data:
                    data["desc_template_pinterest"] = DEFAULT_SETTINGS["desc_template_pinterest"]
                    save_settings(data)
                    settings["desc_template_pinterest"] = DEFAULT_SETTINGS["desc_template_pinterest"]
        except Exception:
            settings = DEFAULT_SETTINGS

    # Fallback to .env in workspace root if R2 credentials are missing
    if not settings.get("r2_account_id") or not settings.get("r2_access_key") or not settings.get("r2_secret_key"):
        dotenv_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
        if dotenv_path.exists():
            try:
                r2_access_key = None
                r2_secret_key = None
                r2_account_id = None
                r2_bucket = None
                with open(dotenv_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip()
                            if k == "CLOUDFLARE_R2_ACCESS_KEY_ID":
                                r2_access_key = v
                            elif k == "CLOUDFLARE_R2_SECRET_ACCESS_KEY":
                                r2_secret_key = v
                            elif k == "CLOUDFLARE_R2_ENDPOINT_URL":
                                import re
                                match = re.search(r"https://([^.]+)\.r2", v)
                                if match:
                                    r2_account_id = match.group(1)
                            elif k == "CLOUDFLARE_R2_BUCKET_NAME":
                                r2_bucket = v
                if r2_account_id and r2_access_key and r2_secret_key:
                    settings["r2_account_id"] = r2_account_id
                    settings["r2_access_key"] = r2_access_key
                    settings["r2_secret_key"] = r2_secret_key
                    settings["r2_bucket"] = "meloscribe-assets"
                    save_settings(settings)
                    print("[Settings] Restored R2 credentials from workspace .env file.")
            except Exception as e:
                print(f"[Settings] Error parsing fallback .env: {e}")

    # Fallback to C:/Dev/credentials.json for all sensitive configurations
    backup_path = Path("C:/Dev/credentials.json")
    if backup_path.exists():
        try:
            with open(backup_path, "r", encoding="utf-8") as f:
                backup_data = json.load(f)
                dirty = False
                
                # Stripe Sandbox & Live keys
                stripe_data = backup_data.get("stripe", {})
                if stripe_data:
                    for k in ["stripe_sandbox_secret_key", "stripe_sandbox_publishable_key", "stripe_live_secret_key", "stripe_live_publishable_key", "stripe_sandbox_webhook_secret", "stripe_live_webhook_secret"]:
                        if not settings.get(k) and stripe_data.get(k):
                            settings[k] = stripe_data[k]
                            dirty = True
                
                # Resend keys
                resend_data = backup_data.get("resend", {})
                if resend_data:
                    if not settings.get("resend_api_key") and resend_data.get("api_key"):
                        settings["resend_api_key"] = resend_data["api_key"]
                        dirty = True
                
                # Gemini
                gemini_data = backup_data.get("gemini", {})
                if gemini_data:
                    if not settings.get("gemini_api_key") and gemini_data.get("api_key"):
                        settings["gemini_api_key"] = gemini_data["api_key"]
                        dirty = True
                
                # Cloudflare R2
                r2_data = backup_data.get("cloudflare_r2", {})
                if r2_data:
                    if not settings.get("r2_account_id") and r2_data.get("account_id"):
                        settings["r2_account_id"] = r2_data["account_id"]
                        dirty = True
                    if not settings.get("r2_access_key") and r2_data.get("access_key_id"):
                        settings["r2_access_key"] = r2_data["access_key_id"]
                        dirty = True
                    if not settings.get("r2_secret_key") and r2_data.get("secret_access_key"):
                        settings["r2_secret_key"] = r2_data["secret_access_key"]
                        dirty = True
                    if not settings.get("r2_bucket") and r2_data.get("bucket_name"):
                        settings["r2_bucket"] = r2_data["bucket_name"]
                        dirty = True
                
                # Social APIs (TikTok, Threads, Instagram)
                if not settings.get("tiktok_client_key") or not settings.get("tiktok_client_secret"):
                    tiktok_data = backup_data.get("tiktok", {})
                    if tiktok_data:
                        if not settings.get("tiktok_client_key") and tiktok_data.get("client_key"):
                            settings["tiktok_client_key"] = tiktok_data["client_key"]
                            dirty = True
                        if not settings.get("tiktok_client_secret") and tiktok_data.get("client_secret"):
                            settings["tiktok_client_secret"] = tiktok_data["client_secret"]
                            dirty = True
                            
                if not settings.get("ig_app_secret"):
                    ig_data = backup_data.get("instagram_facebook", {})
                    if ig_data:
                        settings["ig_app_secret"] = ig_data.get("app_secret", "")
                        dirty = True
                        
                if not settings.get("threads_app_secret"):
                    threads_data = backup_data.get("threads", {})
                    if threads_data:
                        settings["threads_app_secret"] = threads_data.get("app_secret", "")
                        dirty = True

                if dirty:
                    save_settings(settings)
                    print("[Settings] Restored missing credentials from C:\\Dev\\credentials.json")
        except Exception as e:
            print(f"[Settings] Error parsing backup credentials.json: {e}")

    # Add local FFmpeg to PATH for subprocesses (ffmpeg, ffprobe, etc.)
    ffmpeg_bin = str(Path(__file__).resolve().parent.parent.parent / "ffmpeg" / "bin")
    if os.path.exists(ffmpeg_bin) and ffmpeg_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

    # Auto-create directories that end with _dir (except cakewalk_dir)
    for key, val in settings.items():
        if key.endswith("_dir") and val:
            if key == "cakewalk_dir":
                continue
            try:
                os.makedirs(val, exist_ok=True)
            except Exception as e:
                print(f"Failed to create directory {val}: {e}")

    return settings

def save_settings(settings_dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings_dict, f, indent=4)
