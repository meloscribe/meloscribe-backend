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
    "tiktok_client_key": "awe54p8mg3xasm1l",
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
    "threads_upload_easy": True
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

    # Fallback to C:/Dev/credentials.json if Stripe credentials are missing
    if not settings.get("stripe_sandbox_secret_key") or not settings.get("stripe_live_secret_key"):
        backup_path = Path("C:/Dev/credentials.json")
        if backup_path.exists():
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    backup_data = json.load(f)
                    stripe_data = backup_data.get("stripe", {})
                    if stripe_data:
                        settings["stripe_sandbox_secret_key"] = stripe_data.get("stripe_sandbox_secret_key", "")
                        settings["stripe_sandbox_publishable_key"] = stripe_data.get("stripe_sandbox_publishable_key", "")
                        settings["stripe_live_secret_key"] = stripe_data.get("stripe_live_secret_key", "")
                        settings["stripe_live_publishable_key"] = stripe_data.get("stripe_live_publishable_key", "")
                        settings["stripe_sandbox_webhook_secret"] = stripe_data.get("stripe_sandbox_webhook_secret", "")
                        settings["stripe_live_webhook_secret"] = stripe_data.get("stripe_live_webhook_secret", "")
                        save_settings(settings)
                        print("[Settings] Restored Stripe credentials from C:\\Dev\\credentials.json")
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
