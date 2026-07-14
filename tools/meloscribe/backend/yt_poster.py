"""
YouTube API Poster Module
-------------------------
Uploads MP4 videos directly to YouTube.
Supports:
  - Scheduling (publishAt) using the YouTube Data API v3
  - Thumbnail upload for long-form videos (condensed=False)
  - Shorts mode (condensed=True) — posts immediately as public Short
  - Duplicate detection before upload
Returns the final YouTube URL.
"""
import os
import datetime
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from googleapiclient.errors import HttpError

try:
    from meloscribe.backend.yt_auth import get_authenticated_service
except ImportError:
    from yt_auth import get_authenticated_service


def check_duplicate(youtube, song_name: str) -> bool:
    """Returns True if a video with this title already exists on the channel."""
    try:
        resp = youtube.search().list(
            part="snippet",
            forMine=True,
            q=song_name,
            type="video",
            maxResults=5
        ).execute()
        for item in resp.get("items", []):
            if song_name.lower() in item["snippet"]["title"].lower():
                print(f"[YouTube API] Duplicate found: '{item['snippet']['title']}'")
                return True
    except Exception as e:
        print(f"[YouTube API] Warning: Could not check for duplicates: {e}")
    return False


def post_video(video_path: str, title: str, description: str, tags: list = None,
               publish_at_dt: datetime.datetime = None, privacy: str = "private",
               format: str = "full_arrangement", thumbnail_path: str = None,
               skip_duplicate_check: bool = False) -> str | None:
    """
    Uploads a video to YouTube.
    format="viral_part"       → Shorts format (no scheduling, posted public immediately or at publish_at)
    format="full_arrangement"  → Long-form video with optional thumbnail
    publish_at_dt              → Schedule the video (sets status to private + publishAt)
    Returns the YouTube URL or None on failure.
    """
    if not os.path.exists(video_path):
        print(f"[YouTube API] ERROR: Video not found at {video_path}")
        return None

    creds = get_authenticated_service()
    if not creds:
        print("[YouTube API] ERROR: No valid auth token. Please run yt_auth.py first.")
        return None

    youtube = build("youtube", "v3", credentials=creds)

    if not skip_duplicate_check and not check_duplicate(youtube, title.split(" - ")[0]):
        pass  # No duplicate found, proceed
    elif not skip_duplicate_check:
        print("[YouTube API] WARNING: Potential duplicate detected. Upload will continue.")

    if not tags:
        tags = ["piano", "music", "synthesia", "keysight", "tutorial"]

    # Clean title and description of disallowed angle brackets (< and >)
    if title:
        title = title.replace("->", "→").replace("<", "").replace(">", "")
    if description:
        description = description.replace("->", "→").replace("<", "").replace(">", "")

    is_short = (format == "viral_part")
    # Shorts: title must contain #Shorts for discoverability
    if is_short and "#Shorts" not in title:
        title = title + " #Shorts"

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "10"  # Music
        },
        "status": {
            "privacyStatus": "private" if publish_at_dt else ("public" if is_short else privacy),
            "selfDeclaredMadeForKids": False
        }
    }

    if publish_at_dt:
        iso_date = publish_at_dt.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        body["status"]["publishAt"] = iso_date
        print(f"[YouTube API] Scheduling video for {iso_date} (UTC)")

    print(f"\n[YouTube API] Uploading '{os.path.basename(video_path)}' ({'Short' if is_short else 'Long-form'})...")

    try:
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"             Uploading... {int(status.progress() * 100)}%")

        video_id = response.get("id")
        yt_url = f"https://youtu.be/{video_id}"
        print(f"[YouTube API] SUCCESS! Video uploaded at {yt_url}")

        # Upload thumbnail for long-form videos
        if not is_short and thumbnail_path and os.path.exists(thumbnail_path):
            try:
                print(f"[YouTube API] Uploading thumbnail...")
                with open(thumbnail_path, "rb") as tf:
                    import io
                    thumb_media = MediaIoBaseUpload(io.BytesIO(tf.read()), mimetype="image/jpeg")
                    youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
                    print("[YouTube API] Thumbnail uploaded successfully.")
            except HttpError as te:
                print(f"[YouTube API] Warning: Thumbnail upload failed: {te}")

        return yt_url

    except HttpError as e:
        print(f"[YouTube API] An HTTP error {e.resp.status} occurred:\n{e.content}")
        return None
    except Exception as e:
        print(f"[YouTube API] Exception during upload: {e}")
        return None


if __name__ == "__main__":
    print("This module is meant to be imported.")
