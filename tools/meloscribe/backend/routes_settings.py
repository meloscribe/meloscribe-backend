import os
import sys
import json
import requests
import threading
import subprocess
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse

from shared import (
    TOOLS_DIR,
    CREATION_FLAGS,
    load_settings
)
from settings import save_settings

router = APIRouter()

@router.get("/callback")
def oauth_callback(code: str, state: str = None):
    """
    Unified OAuth callback proxy and direct processor.
    - If state == 'threads', directly handles Threads token exchange and saves it.
    - Otherwise, forwards to localhost:8080 (e.g. for TikTok/other local auth).
    """
    if state == "threads":
        try:
            settings_path = TOOLS_DIR / "meloscribe" / "backend" / "settings.json"
            tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "threads_tokens.json"
            
            with open(settings_path, "r", encoding="utf-8") as f:
                settings_data = json.load(f)
            
            app_id = settings_data.get("threads_app_id", "2376057852870646")
            app_secret = settings_data.get("threads_app_secret", "")
            redirect_uri = "https://wooing-encrust-ladle.ngrok-free.dev/callback"

            # Exchange code for short-lived token
            resp = requests.post("https://graph.threads.net/oauth/access_token", data={
                "client_id": app_id,
                "client_secret": app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code
            })
            
            if resp.status_code != 200:
                return HTMLResponse(
                    content=f"<h1>Threads short-lived token exchange failed:</h1><pre>{resp.status_code} - {resp.text}</pre>",
                    status_code=400
                )
            
            short_data = resp.json()
            short_token = short_data.get("access_token")
            user_id = short_data.get("user_id")

            # Exchange short-lived token for long-lived token
            long_resp = requests.get("https://graph.threads.net/access_token", params={
                "grant_type": "th_exchange_token",
                "client_secret": app_secret,
                "access_token": short_token
            })
            
            if long_resp.status_code != 200:
                return HTMLResponse(
                    content=f"<h1>Threads long-lived token exchange failed:</h1><pre>{long_resp.status_code} - {long_resp.text}</pre>",
                    status_code=400
                )
            
            long_data = long_resp.json()
            long_token = long_data.get("access_token")

            # Fetch profile to get username
            me_resp = requests.get("https://graph.threads.net/v1.0/me", params={
                "fields": "id,username",
                "access_token": long_token
            })
            username = "unknown"
            if me_resp.status_code == 200:
                username = me_resp.json().get("username", "unknown")

            # Save credentials
            save_data = {
                "access_token": long_token,
                "threads_user_id": str(user_id),
                "username": username
            }
            with open(tokens_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=4)

            print(f"[Threads Auth] Successfully authorized as {username}. Token saved.")
            return HTMLResponse(
                content=f"<h1>Threads Autorisierung erfolgreich!</h1><p>Du bist nun als <b>@{username}</b> angemeldet. Du kannst diesen Tab schliessen.</p>",
                status_code=200
            )

        except Exception as e:
            return HTMLResponse(content=f"<h1>Internal Error during Threads exchange: {e}</h1>", status_code=500)

    try:
        url = f"http://localhost:8080/?code={code}"
        if state:
            url += f"&state={state}"
        resp = requests.get(url)
        return HTMLResponse(content=resp.text, status_code=resp.status_code)
    except Exception as e:
        return HTMLResponse(content=f"<h1>Forwarding failed: {e}</h1>", status_code=500)

# -------------------------------------------------------------------
# Pydantic Request Models
# -------------------------------------------------------------------
class InstagramAuthRequest(BaseModel):
    short_lived_token: Optional[str] = None

class ThreadsAuthRequest(BaseModel):
    access_token: Optional[str] = None

class PinterestSettingsPayload(BaseModel):
    pinterest_app_id: str
    pinterest_app_secret: str
    pinterest_access_token: str
    pinterest_board_easy: str
    pinterest_board_intermediate: str
    desc_template_pinterest: str

# -------------------------------------------------------------------
# Settings & Sync Credentials
# -------------------------------------------------------------------
@router.get("/api/settings")
def get_settings():
    return load_settings()

@router.post("/api/settings")
async def update_settings(request: Request):
    data = await request.json()
    save_settings(data)
    threading.Thread(target=sync_credentials_route_internal, daemon=True).start()
    return {"status": "success"}

@router.post("/api/server/sync-credentials")
def sync_credentials_route():
    res = sync_credentials_route_internal()
    return res

def sync_credentials_route_internal():
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    if not os.path.exists(key_path):
        print(f"[Sync Settings] ERROR: SSH Key not found at {key_path}")
        return {"status": "error", "message": f"SSH Key not found at {key_path}"}
        
    backend_dir = Path(__file__).resolve().parent
    files_to_sync = ["settings.json", "ig_tokens.json", "threads_tokens.json", "tiktok_tokens.json", "yt_tokens.json", "pinterest_tokens.json", "api_key.txt"]
    
    synced_files = []
    errors = []
    
    print(f"[Sync Settings] Starting sync of credential files to server...")
    for fname in files_to_sync:
        local_path = backend_dir / fname
        if local_path.exists():
            print(f"[Sync Settings] Syncing {fname} to OCI...")
            cmd = [
                "scp", "-i", key_path,
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=5",
                "-o", "IdentitiesOnly=yes",
                str(local_path),
                f"ubuntu@{server_ip}:/home/ubuntu/meloscribe/tools/meloscribe/backend/{fname}"
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=CREATION_FLAGS)
                if res.returncode == 0:
                    synced_files.append(fname)
                    print(f"[Sync Settings] Sync success: {fname}")
                else:
                    err_msg = res.stderr.strip()
                    errors.append(f"Failed {fname}: {err_msg}")
                    print(f"[Sync Settings] Sync failed for {fname}: {err_msg}")
            except Exception as e:
                errors.append(f"Error {fname}: {str(e)}")
                print(f"[Sync Settings] Exception syncing {fname}: {e}")
                
    if errors:
        print(f"[Sync Settings] Sync partially failed. Synced: {synced_files}. Errors: {errors}")
        return {"status": "error", "message": f"Sync partially failed. Synced: {synced_files}. Errors: {errors}"}
    print(f"[Sync Settings] Successfully synchronized credentials files to OCI: {synced_files}")
    return {"status": "success", "message": f"Successfully synchronized credentials files to OCI: {synced_files}"}

# -------------------------------------------------------------------
# TikTok Auth
# -------------------------------------------------------------------
@router.get("/api/tiktok/status")
def tiktok_status():
    tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "tiktok_tokens.json"
    if not tokens_path.exists():
        return {"authorized": False, "message": "Not connected. Use /api/tiktok/authorize to connect."}
    try:
        import sys
        sys.path.insert(0, str(TOOLS_DIR / "meloscribe" / "backend"))
        from tiktok_auth import get_valid_token
        token = get_valid_token()
        if not token:
            return {"authorized": False, "message": "Access token expired and failed to refresh."}
        
        url = "https://open.tiktokapis.com/v2/user/info/?fields=open_id,union_id,avatar_url"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(url, headers=headers, timeout=5)
        
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            user_data = data.get("user", {})
            return {
                "authorized": True,
                "open_id": user_data.get("open_id", "unknown"),
                "avatar_url": user_data.get("avatar_url", "")
            }
        else:
            return {"authorized": False, "message": f"TikTok API rejected token: {resp.text}"}
    except Exception as e:
        return {"authorized": False, "message": f"Validation error: {e}"}

@router.post("/api/tiktok/authorize")
def tiktok_authorize():
    import importlib.util
    auth_path = str(TOOLS_DIR / "meloscribe" / "backend" / "tiktok_auth.py")
    spec = importlib.util.spec_from_file_location("tiktok_auth", auth_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    
    threading.Thread(target=mod.run_initial_auth, args=(False,), daemon=True).start()
    
    import time
    time.sleep(0.5)
    
    auth_url = getattr(mod, "LAST_AUTH_URL", None)
    print(f"[TikTok Auth] Generated URL: {auth_url}")
    return {
        "status": "opening browser for TikTok authorization...",
        "url": auth_url
    }

@router.post("/api/tiktok/sync")
async def tiktok_sync_now():
    def _run():
        import importlib.util
        sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "tiktok_sync.py")
        spec = importlib.util.spec_from_file_location("tiktok_sync", sync_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.sync_tiktok()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}

# -------------------------------------------------------------------
# Instagram Auth
# -------------------------------------------------------------------
@router.get("/api/instagram/status")
def instagram_status():
    tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "ig_tokens.json"
    if not tokens_path.exists():
        return {"authorized": False, "message": "Not connected."}
    try:
        tokens = json.loads(tokens_path.read_text())
        access_token = tokens.get("fb_access_token") or tokens.get("access_token")
        if not access_token:
            return {"authorized": False, "message": "No access token found in ig_tokens.json."}
            
        url = f"https://graph.facebook.com/v18.0/me?access_token={access_token}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return {
                "authorized": True,
                "page_name": tokens.get("fb_page_name", "unknown")
            }
        else:
            err_msg = resp.json().get("error", {}).get("message", "Invalid Token")
            return {"authorized": False, "message": f"Graph API rejected token: {err_msg}"}
    except Exception as e:
        return {"authorized": False, "message": f"Validation error: {e}"}

@router.post("/api/instagram/sync")
async def instagram_sync_now():
    def _run():
        import importlib.util
        sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "ig_sync.py")
        spec = importlib.util.spec_from_file_location("ig_sync", sync_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.sync_instagram()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}

@router.post("/api/instagram/authorize")
def instagram_authorize(req: Optional[InstagramAuthRequest] = None):
    if req and req.short_lived_token:
        import sys
        sys.path.insert(0, str(TOOLS_DIR / "meloscribe" / "backend"))
        try:
            from ig_setup import setup_instagram_account
            success = setup_instagram_account(req.short_lived_token)
            if success:
                return {"status": "success", "message": "Instagram and Facebook successfully authorized."}
            else:
                return {"status": "error", "message": "Failed to exchange token. Check backend console logs."}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        def _run():
            import importlib.util
            auth_path = str(TOOLS_DIR / "meloscribe" / "backend" / "ig_auth.py")
            spec = importlib.util.spec_from_file_location("ig_auth", auth_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.run_instagram_auth()
        threading.Thread(target=_run, daemon=True).start()
        return {"status": "opening browser for Instagram authorization..."}

# -------------------------------------------------------------------
# YouTube Auth
# -------------------------------------------------------------------
@router.get("/api/youtube/status")
def youtube_status():
    tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "yt_tokens.json"
    if not tokens_path.exists():
        return {"authorized": False, "message": "Not connected."}
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        
        with open(tokens_path, "r") as f:
            creds_data = json.load(f)
            
        scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly"
        ]
        creds = Credentials.from_authorized_user_info(creds_data, scopes)
        
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(tokens_path, "w") as f:
                    f.write(creds.to_json())
            else:
                return {"authorized": False, "message": "Token expired and refresh token is invalid."}
                
        url = "https://www.googleapis.com/youtube/v3/channels?part=id&mine=true"
        headers = {"Authorization": f"Bearer {creds.token}"}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            return {"authorized": True}
        else:
            return {"authorized": False, "message": f"YouTube API rejected token: {resp.text}"}
    except Exception as e:
        return {"authorized": False, "message": f"Validation error: {e}"}

@router.post("/api/youtube/sync")
async def youtube_sync_now():
    def _run():
        import importlib.util
        sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "yt_sync.py")
        spec = importlib.util.spec_from_file_location("yt_sync", sync_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.sync_youtube()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}

@router.post("/api/youtube/authorize")
def youtube_authorize():
    def _run():
        import importlib.util
        auth_path = str(TOOLS_DIR / "meloscribe" / "backend" / "yt_auth.py")
        spec = importlib.util.spec_from_file_location("yt_auth", auth_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.get_authenticated_service()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "opening browser for YouTube authorization..."}

# -------------------------------------------------------------------
# Threads Auth
# -------------------------------------------------------------------
@router.get("/api/threads/status")
def threads_status():
    tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "threads_tokens.json"
    if not tokens_path.exists():
        return {"authorized": False, "message": "Not connected."}
    try:
        tokens = json.loads(tokens_path.read_text())
        access_token = tokens.get("access_token")
        if not access_token:
            return {"authorized": False, "message": "No access token."}
            
        url = f"https://graph.threads.net/v1.0/me?fields=id,username&access_token={access_token}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "authorized": True,
                "username": data.get("username", tokens.get("username", "unknown"))
            }
        else:
            return {"authorized": False, "message": f"Threads API rejected token: {resp.text}"}
    except Exception as e:
        return {"authorized": False, "message": f"Validation error: {e}"}

@router.post("/api/threads/authorize")
def threads_authorize(req: Optional[ThreadsAuthRequest] = None):
    if req and req.access_token:
        try:
            url = f"https://graph.threads.net/v1.0/me?fields=id,username&access_token={req.access_token}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                threads_user_id = data.get("id")
                
                tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "threads_tokens.json"
                save_data = {
                    "access_token": req.access_token,
                    "threads_user_id": threads_user_id,
                    "username": data.get("username", "unknown")
                }
                with open(tokens_path, "w") as f:
                    json.dump(save_data, f, indent=4)
                    
                return {
                    "status": "success", 
                    "message": f"Threads successfully connected as '{data.get('username')}'."
                }
            else:
                err_msg = resp.json().get("error", {}).get("message", "Invalid Token")
                return {"status": "error", "message": f"API rejected token: {err_msg}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        def _run():
            import importlib.util
            auth_path = str(TOOLS_DIR / "meloscribe" / "backend" / "threads_auth.py")
            spec = importlib.util.spec_from_file_location("threads_auth", auth_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.run_threads_auth()
        threading.Thread(target=_run, daemon=True).start()
        return {"status": "opening browser for Threads authorization..."}

# -------------------------------------------------------------------
# Pinterest Settings & Auth
# -------------------------------------------------------------------
@router.get("/api/pinterest/status")
def pinterest_status():
    tokens_path = TOOLS_DIR / "pinterest_tokens.json"
    if not tokens_path.exists():
        tokens_path = Path(__file__).resolve().parent / "pinterest_tokens.json"
    if not tokens_path.exists():
        return {"authorized": False, "message": "Not connected."}
    try:
        with open(tokens_path, "r", encoding="utf-8") as f:
            tokens = json.load(f)
        access_token = tokens.get("pinterest_access_token")
        if not access_token:
            return {"authorized": False, "message": "No access token."}
            
        url = "https://api.pinterest.com/v5/user_account"
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "authorized": True,
                "username": data.get("username", "connected")
            }
        elif resp.status_code == 401:
            # Try to refresh using stored refresh_token
            refresh_token = tokens.get("pinterest_refresh_token")
            app_id = tokens.get("pinterest_app_id", "")
            app_secret = tokens.get("pinterest_app_secret", "")
            if refresh_token and app_id and app_secret:
                try:
                    import base64
                    credentials = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()
                    refresh_resp = requests.post(
                        "https://api.pinterest.com/v5/oauth/token",
                        headers={
                            "Authorization": f"Basic {credentials}",
                            "Content-Type": "application/x-www-form-urlencoded"
                        },
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": refresh_token
                        },
                        timeout=10
                    )
                    if refresh_resp.status_code == 200:
                        new_tokens = refresh_resp.json()
                        tokens["pinterest_access_token"] = new_tokens.get("access_token", access_token)
                        if "refresh_token" in new_tokens:
                            tokens["pinterest_refresh_token"] = new_tokens["refresh_token"]
                        with open(tokens_path, "w", encoding="utf-8") as f:
                            json.dump(tokens, f, indent=2, ensure_ascii=False)
                        print("[Pinterest] Token auto-refreshed successfully.")
                        return {"authorized": True, "username": "auto-refreshed"}
                    else:
                        return {"authorized": False, "message": f"Token expired and refresh failed: {refresh_resp.text}"}
                except Exception as re:
                    return {"authorized": False, "message": f"Token refresh error: {re}"}
            return {"authorized": False, "message": f"Token expired (401). Please generate a new token in Pinterest Settings."}
        else:
            return {"authorized": False, "message": f"Pinterest API error {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"authorized": False, "message": f"Validation error: {e}"}

@router.post("/api/pinterest/sync")
async def pinterest_sync_now():
    def _run():
        import importlib.util
        sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "pinterest_sync.py")
        spec = importlib.util.spec_from_file_location("pinterest_sync", sync_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.sync_pinterest()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}

@router.get("/api/pinterest/settings")
def get_pinterest_settings():
    tokens_path = TOOLS_DIR / "pinterest_tokens.json"
    if not tokens_path.exists():
        tokens_path = Path(__file__).resolve().parent / "pinterest_tokens.json"
    if not tokens_path.exists():
        return {
            "pinterest_app_id": "",
            "pinterest_app_secret": "",
            "pinterest_access_token": "",
            "pinterest_board_easy": "",
            "pinterest_board_intermediate": "",
            "desc_template_pinterest": ""
        }
    try:
        with open(tokens_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read Pinterest settings: {e}")

@router.post("/api/pinterest/settings")
def save_pinterest_settings(payload: PinterestSettingsPayload):
    tokens_path = TOOLS_DIR / "pinterest_tokens.json"
    if not tokens_path.exists():
        tokens_path = Path(__file__).resolve().parent / "pinterest_tokens.json"
        
    data = payload.dict()
    try:
        with open(tokens_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        key_path = r"C:\Dev\ssh-key-2026-05-07.key"
        server_ip = "152.70.23.171"
        if os.path.exists(key_path):
            cmd = [
                "scp", "-i", key_path,
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=5",
                "-o", "IdentitiesOnly=yes",
                str(tokens_path),
                f"ubuntu@{server_ip}:/home/ubuntu/meloscribe/tools/meloscribe/backend/pinterest_tokens.json"
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, creationflags=CREATION_FLAGS)
            
        return {"status": "success", "message": "Pinterest settings saved and synced to OCI VM!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save Pinterest settings: {e}")
