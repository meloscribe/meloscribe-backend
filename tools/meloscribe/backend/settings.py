import json
from pathlib import Path
import os

SETTINGS_FILE = Path(__file__).parent / "settings.json"

DEFAULT_SETTINGS = {
    "musescore_dir": str(Path(__file__).resolve().parent.parent.parent.parent / "Scores"),
    "musescore_exe": r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    "cakewalk_dir": r"C:\Cakewalk Projects",
    "keysight_dir": r"C:\Dev\meloscribe\Keysight export",
    "tiktok_dir": r"C:\Dev\meloscribe\TikToks",
    "covers_dir": r"C:\Dev\meloscribe\Covers",
    "packages_dir": r"C:\Dev\meloscribe\packages",
    "keysight_exe": r"C:\Program Files (x86)\Steam\steamapps\common\Keysight\Keysight\Binaries\Win64\Keysight-Win64-Shipping.exe",
    "browser_exec": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    "browser_user_data": os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\User Data"),
    "pushbullet_token": "",
    "schedule_interval_days": 3,
    "localUpload": False,
    "desc_template_youtube": (
        "Enjoy this piano arrangement of {song} by {author}! "
        "Whether you're here to listen or want to learn this piece yourself - I've got you covered.\n\n"
        "🎹 Sheet Music (PDF) & MIDI files available here: https://ko-fi.com/meloscribe?utm_source=youtube&utm_medium={medium}\n\n"
        "Check out my channel for more aesthetic piano covers and tutorials!\n\n"
        "#piano #pianocover #pianotutorial #music #synthesia #keysight"
    ),
    "desc_template_instagram": (
        "🎹 {song}{label} - {author}\n\n"
        "Sheet Music & MIDI -> Link in Bio (Ko-Fi)\n\n"
        "#piano #pianocover #pianotutorial #synthesia #music #pianomusic"
    ),
    "desc_template_facebook": (
        "🎹 {song}{label} - {author} | Piano Cover\n\n"
        "Sheet Music & MIDI: https://ko-fi.com/meloscribe?utm_source=facebook&utm_medium={medium}\n\n"
        "#piano #pianocover #synthesia #music"
    ),
    "desc_template_threads": (
        "🎹 {song}{label} - {author}\n\n"
        "Sheet Music & MIDI -> Ko-Fi (link in bio)\n\n"
        "#piano #pianocover #pianotutorial #synthesia #music"
    ),
    "desc_template_kofi": (
        "Get the learning package for my '{song}' tutorial! This download includes:\n\n"
        "PDF sheet music, a high-quality MIDI file in original speed, a bonus slow-speed MIDI file for easy practice, "
        "and both 2K-quality tutorial videos (Original Version, Slow Version) for offline learning "
        "all packed into a ZIP, because sometimes files get missing after uploading.\n\n"
        "This sheet music/MIDI contains all unique musical sections (Intro, Verse, Chorus, Bridge) as shown in the video. "
        "Repetitions may be omitted for brevity, but all distinct parts are included."
    ),
    "desc_template_kofi_original_full": (
        "Get the learning package for my '{song}' tutorial! This download includes:\n\n"
        "PDF sheet music, a high-quality MIDI file in original speed, a bonus slow-speed MIDI file for easy practice, "
        "and both 2K-quality tutorial videos (Original Version, Slow Version) for offline learning "
        "all packed into a ZIP, because sometimes files get missing after uploading.\n\n"
        "This sheet music/MIDI contains all unique musical sections (Intro, Verse, Chorus, Bridge) as shown in the video. "
        "Repetitions may be omitted for brevity, but all distinct parts are included."
    ),
    "desc_template_kofi_original_condensed": (
        "Get the condensed learning package for my '{song}' tutorial! This download contains the viral/short part of the song and includes:\n\n"
        "PDF sheet music, a high-quality MIDI file in original speed, a bonus slow-speed MIDI file for easy practice, "
        "and both 2K-quality tutorial videos (Original Version, Slow Version) for offline learning "
        "all packed into a ZIP, because sometimes files get missing after uploading.\n\n"
        "This sheet music/MIDI contains all unique musical sections as shown in the video."
    ),
    "desc_template_kofi_easy_full": (
        "Get the easy learning package for my '{song}' tutorial! This download includes:\n\n"
        "PDF easy sheet music, a high-quality simplified MIDI file, a bonus slow-speed simplified MIDI file for easy practice, "
        "and both 2K-quality easy tutorial videos (Easy Version, Slow Easy Version) for offline learning "
        "all packed into a ZIP, because sometimes files get missing after uploading.\n\n"
        "This simplified sheet music/MIDI contains all unique musical sections (Intro, Verse, Chorus, Bridge) as shown in the video. "
        "Repetitions may be omitted for brevity, but all distinct parts are included."
    ),
    "desc_template_kofi_easy_condensed": (
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
