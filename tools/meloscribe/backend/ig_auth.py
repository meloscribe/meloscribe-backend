"""
Instagram/Facebook OAuth Flow + Token Manager
----------------------------------------------
Opens browser for Facebook Login, captures code via ngrok proxy,
and exchanges it for a permanent Page access token.
"""
import os
import json
import time
import threading
import webbrowser
import urllib.parse
import http.server
import requests
from pathlib import Path

APP_ID = "26975285422066567"
APP_SECRET = "70752bda986825b8e63b8ad2c07c93fc"
REDIRECT_URI = "https://wooing-encrust-ladle.ngrok-free.dev/callback"
SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish"
TOKENS_PATH = Path(__file__).parent / "ig_tokens.json"

try:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from settings import load_settings
    _settings = load_settings()
    if _settings.get("ig_app_id"):
        APP_ID = str(_settings.get("ig_app_id"))
    if _settings.get("ig_app_secret"):
        APP_SECRET = str(_settings.get("ig_app_secret"))
except Exception as _e:
    print(f"[Instagram Auth] Warning: could not load app credentials from settings: {_e}")

def _open_browser(url: str):
    try:
        import os
        if hasattr(os, "startfile"):
            os.startfile(url)
            print(f"[Auth] Opened browser via os.startfile: {url}")
            return
    except Exception as e:
        print(f"[Auth] os.startfile failed: {e}")

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from settings import load_settings
        settings = load_settings()
        browser_exe = settings.get("browser_exec")
        if browser_exe and os.path.exists(browser_exe):
            import subprocess
            subprocess.Popen([browser_exe, url])
            print(f"[Auth] Opened browser via executable: {browser_exe}")
            return
    except Exception as e:
        print(f"[Auth] Failed to launch configured browser: {e}")
        
    import webbrowser
    webbrowser.open(url)
    print("[Auth] Opened browser via system default webbrowser module")

def run_instagram_auth() -> bool:
    captured = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                captured["code"] = params["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h1>Authorization successful! You can close this tab.</h1>")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h1>No code received.</h1>")
        def log_message(self, *args): pass

    server = http.server.HTTPServer(("localhost", 8080), CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request)
    server_thread.daemon = True
    server_thread.start()

    auth_url = (
        "https://www.facebook.com/v19.0/dialog/oauth?"
        + urllib.parse.urlencode({
            "client_id": APP_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "response_type": "code",
            "state": "fb"
        })
    )

    print(f"[Instagram Auth] Opening browser for authorization...")
    _open_browser(auth_url)

    print("[Instagram Auth] Waiting for callback (max 120s)...")
    server_thread.join(timeout=120)

    if "code" not in captured:
        print("[Instagram Auth] ERROR: No authorization code received within timeout.")
        return False

    code = captured["code"]
    print(f"[Instagram Auth] Code received. Exchanging for short-lived user token...")

    resp = requests.get("https://graph.facebook.com/v19.0/oauth/access_token", params={
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
        "client_secret": APP_SECRET,
        "code": code
    })

    if resp.status_code != 200:
        print(f"[Instagram Auth] Token exchange failed: {resp.status_code} - {resp.text}")
        return False

    short_lived_token = resp.json().get("access_token")
    if not short_lived_token:
        print("[Instagram Auth] No access token in response.")
        return False

    # Call setup_instagram_account to complete token exchange and save permanent token
    from ig_setup import setup_instagram_account
    success = setup_instagram_account(short_lived_token)
    return success

if __name__ == "__main__":
    run_instagram_auth()
