"""
Facebook Graph API Poster
--------------------------
Uploads Videos / Reels to a Facebook Page using the Graph API.
Supports scheduling via scheduled_publish_time.
condensed=True  → posts as a Reel (short vertical video)
condensed=False → posts as a regular video with thumbnail
"""
import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone

TOKENS_PATH = Path(__file__).parent / "ig_tokens.json"
GRAPH_URL = "https://graph.facebook.com/v19.0"


def _get_creds():
    if not TOKENS_PATH.exists():
        return None, None
    with open(TOKENS_PATH, "r") as f:
        d = json.load(f)
    return d.get("access_token"), d.get("fb_page_id")


def post_comment(video_id: str, message: str, pin: bool = True) -> str | None:
    """Post a comment to a Facebook Video or Reel, and optionally pin it."""
    token, _ = _get_creds()
    if not token or not video_id or not message:
        return None
    url = f"{GRAPH_URL}/{video_id}/comments"
    resp = requests.post(url, data={"message": message, "access_token": token})
    if resp.status_code in (200, 201) and resp.json().get("id"):
        comment_id = resp.json()["id"]
        print(f"[Facebook API] Comment posted successfully (ID: {comment_id})")
        if pin:
            try:
                pin_resp = requests.post(f"{GRAPH_URL}/{comment_id}", data={"is_pinned": True, "access_token": token})
                if pin_resp.status_code == 200 and pin_resp.json().get("success"):
                    print(f"[Facebook API] Comment pinned to top successfully!")
            except Exception as pe:
                print(f"[Facebook API] Warning: Could not pin comment: {pe}")
        return comment_id
    else:
        print(f"[Facebook API] Failed to post comment: {resp.status_code} {resp.text[:200]}")
        return None


def post_video(video_path: str, title: str, description: str,
               format: str = "viral_part", thumbnail_path: str = None,
               publish_at_dt: datetime = None, comment_text: str = None) -> bool:
    """
    Upload a video to a Facebook Page.
    format="viral_part"       → Reel (no thumbnail required, uses vertical video)
    format="full_arrangement"  → Long-form video with optional thumbnail
    publish_at_dt              → Schedule the post (must be in the future)
    comment_text               → Automated comment with direct link to post after upload
    """
    if not os.path.exists(video_path):
        print(f"[Facebook API] ERROR: Video not found at {video_path}")
        return False

    token, page_id = _get_creds()
    if not token or not page_id:
        print("[Facebook API] ERROR: No valid FB token. Run ig_setup.py first.")
        return False

    is_reel = (format == "viral_part")
    print(f"\n[Facebook API] Uploading '{os.path.basename(video_path)}' as {'Reel' if is_reel else 'Video'}...")

    if is_reel:
        # Upload as Reel via the /reels endpoint
        upload_url = f"{GRAPH_URL}/{page_id}/video_reels"
        params = {
            "upload_phase": "start",
            "access_token": token
        }
        init_resp = requests.post(upload_url, data=params)
        if init_resp.status_code != 200:
            print(f"[Facebook API] Reel init failed: {init_resp.status_code} {init_resp.text[:200]}")
            return False
        
        reel_init = init_resp.json()
        upload_session_id = reel_init.get("video_id")
        upload_target = reel_init.get("upload_url", f"{GRAPH_URL}/{page_id}/video_reels")

        # Upload binary
        file_size = os.path.getsize(video_path)
        with open(video_path, "rb") as vf:
            upload_resp = requests.post(
                upload_target,
                headers={
                    "Authorization": f"OAuth {token}",
                    "offset": "0",
                    "file_size": str(file_size)
                },
                data=vf
            )
        if upload_resp.status_code not in (200, 201):
            print(f"[Facebook API] Reel upload failed: {upload_resp.status_code} {upload_resp.text[:200]}")
            return False

        # Finish / schedule
        finish_params = {
            "upload_phase": "finish",
            "video_id": upload_session_id,
            "access_token": token,
            "video_state": "SCHEDULED" if publish_at_dt else "PUBLISHED",
            "description": description,
            "title": title
        }
        if publish_at_dt:
            finish_params["scheduled_publish_time"] = int(publish_at_dt.astimezone(timezone.utc).timestamp())

        finish_resp = requests.post(upload_url, data=finish_params)
        if finish_resp.status_code == 200 and finish_resp.json().get("success"):
            print(f"[Facebook API] SUCCESS! Reel {'scheduled' if publish_at_dt else 'published'}.")
            if comment_text and not publish_at_dt:
                time.sleep(3)
                post_comment(upload_session_id, comment_text, pin=True)
            return True
        else:
            print(f"[Facebook API] Finish failed: {finish_resp.status_code} {finish_resp.text[:300]}")
            return False

    else:
        # Upload as a regular long-form Video
        # Facebook Graph API limits direct multipart uploads to ~25MB.
        # For large / long-form widescreen videos, we use Cloudflare R2 presigned URL ('file_url')
        from settings import load_settings
        settings = load_settings()
        
        r2_account_id = settings.get("r2_account_id")
        r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id")
        r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key")
        r2_bucket     = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-sheets")
        
        file_size = os.path.getsize(video_path)
        use_r2 = (file_size > 25 * 1024 * 1024) and bool(r2_account_id and r2_access_key and r2_secret_key)
        
        if use_r2:
            import boto3
            from botocore.config import Config
            import uuid
            
            s3_client = boto3.client(
                's3',
                endpoint_url=f"https://{r2_account_id}.r2.cloudflarestorage.com",
                aws_access_key_id=r2_access_key,
                aws_secret_access_key=r2_secret_key,
                config=Config(signature_version='s3v4')
            )
            
            temp_key = f"temp_uploads/{uuid.uuid4().hex[:8]}_{os.path.basename(video_path)}"
            print(f"[Facebook API] Uploading temporary video to R2 ({file_size / (1024*1024):.1f} MB): {temp_key}...")
            s3_client.upload_file(video_path, r2_bucket, temp_key, ExtraArgs={'ContentType': 'video/mp4'})
            
            presigned_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': r2_bucket, 'Key': temp_key},
                ExpiresIn=3600
            )
            print("[Facebook API] Video staged to R2. Submitting to Facebook Graph API via file_url...")
            
            upload_url = f"https://graph-video.facebook.com/v19.0/{page_id}/videos"
            params = {
                "title": title,
                "description": description,
                "access_token": token,
                "file_url": presigned_url
            }
            if publish_at_dt:
                params["scheduled_publish_time"] = int(publish_at_dt.astimezone(timezone.utc).timestamp())
                params["published"] = "false"
                
            resp = requests.post(upload_url, data=params)
            
            # Clean up R2 file immediately
            try:
                s3_client.delete_object(Bucket=r2_bucket, Key=temp_key)
                print(f"[Facebook API] Deleted temporary R2 file: {temp_key}")
            except Exception:
                pass
                
            if resp.status_code in (200, 201) and resp.json().get("id"):
                video_id = resp.json()["id"]
                print(f"[Facebook API] SUCCESS! Video submitted via file_url (ID: {video_id})")
                if thumbnail_path and os.path.exists(thumbnail_path):
                    with open(thumbnail_path, "rb") as tf:
                        requests.post(
                            f"https://graph-video.facebook.com/v19.0/{video_id}",
                            data={"access_token": token},
                            files={"thumb": tf}
                        )
                if comment_text and not publish_at_dt:
                    time.sleep(3)
                    post_comment(video_id, comment_text, pin=True)
                return True
            else:
                print(f"[Facebook API] Upload via file_url failed: {resp.status_code} {resp.text[:300]}")
                return False
        else:
            upload_url = f"https://graph-video.facebook.com/v19.0/{page_id}/videos"
            params = {
                "title": title,
                "description": description,
                "access_token": token,
            }
            if publish_at_dt:
                params["scheduled_publish_time"] = int(publish_at_dt.astimezone(timezone.utc).timestamp())
                params["published"] = "false"

            with open(video_path, "rb") as vf:
                files = {"source": (os.path.basename(video_path), vf, "video/mp4")}
                resp = requests.post(upload_url, data=params, files=files)
            
            if resp.status_code in (200, 201) and resp.json().get("id"):
                video_id = resp.json()["id"]
                print(f"[Facebook API] SUCCESS! Video uploaded (ID: {video_id})")

                if thumbnail_path and os.path.exists(thumbnail_path):
                    with open(thumbnail_path, "rb") as tf:
                        thumb_resp = requests.post(
                            f"https://graph-video.facebook.com/v19.0/{video_id}",
                            data={"access_token": token},
                            files={"thumb": tf}
                        )
                        if thumb_resp.status_code == 200:
                            print("[Facebook API] Thumbnail set.")
                if comment_text and not publish_at_dt:
                    time.sleep(3)
                    post_comment(video_id, comment_text, pin=True)
                return True
            else:
                print(f"[Facebook API] Upload failed: {resp.status_code} {resp.text[:300]}")
                return False
