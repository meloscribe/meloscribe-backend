"""
TikTok OAuth Flow + Token Manager (PKCE)
------------------------------------------
First run:  python tiktok_auth.py
This opens a browser, you authorize, and the tokens are saved to tiktok_tokens.json.
After that, tiktok_sync.py uses this module to auto-refresh as needed.
"""
import os
import json
import time
import base64
import hashlib
import secrets
import threading
import webbrowser
import urllib.parse
import http.server
import requests
from pathlib import Path


def _generate_pkce():
    """Generate code_verifier and code_challenge for PKCE."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode('ascii')
    digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
    return code_verifier, code_challenge

# -------------------------------------------------------
CLIENT_KEY    = "sbaw7fguduihq6she7"
CLIENT_SECRET = ""
LOCAL_PORT    = 8080
REDIRECT_URI  = "https://api.meloscribe.dev/callback"
SCOPES        = "user.info.basic,video.list,video.upload,video.publish"
TOKENS_PATH   = Path(__file__).parent / "tiktok_tokens.json"

TOKEN_URL  = "https://open.tiktokapis.com/v2/oauth/token/"
AUTH_BASE  = "https://www.tiktok.com/v2/auth/authorize/"

try:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from settings import load_settings
    _settings = load_settings()
    if _settings.get("tiktok_client_key"):
        CLIENT_KEY = str(_settings.get("tiktok_client_key"))
    if _settings.get("tiktok_client_secret"):
        CLIENT_SECRET = str(_settings.get("tiktok_client_secret"))
except Exception:
    pass
LAST_AUTH_URL = None




def _load_tokens() -> dict | None:
    if TOKENS_PATH.exists():
        with open(TOKENS_PATH, "r") as f:
            return json.load(f)
    return None


def _save_tokens(data: dict):
    with open(TOKENS_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[TikTok] Tokens saved to {TOKENS_PATH}")


def _refresh_access_token(refresh_token: str) -> dict | None:
    """Exchange refresh_token for a fresh access_token."""
    resp = requests.post(TOKEN_URL, data={
        "client_key":     CLIENT_KEY,
        "client_secret":  CLIENT_SECRET,
        "grant_type":     "refresh_token",
        "refresh_token":  refresh_token,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if resp.status_code == 200:
        data = resp.json()
        # Save immediately so the next call has fresh tokens
        tokens = _load_tokens() or {}
        tokens.update({
            "access_token":  data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_at":    time.time() + data.get("expires_in", 86400),
            "open_id":       data.get("open_id", tokens.get("open_id")),
        })
        _save_tokens(tokens)
        print("[TikTok] Token refreshed successfully.")
        return tokens
    else:
        print(f"[TikTok] Refresh failed: {resp.status_code} — {resp.text}")
        return None


def get_valid_token() -> str | None:
    """
    Returns a valid access_token.
    Refreshes automatically if expired.
    Returns None if no tokens exist at all (first-time auth needed).
    """
    tokens = _load_tokens()
    if not tokens:
        print("[TikTok] No tokens found. Run: python tiktok_auth.py to authorize.")
        return None

    # Check expiry (refresh 5 minutes early)
    if time.time() >= tokens.get("expires_at", 0) - 300:
        print("[TikTok] Access token expired, refreshing...")
        tokens = _refresh_access_token(tokens["refresh_token"])
        if not tokens:
            return None

    return tokens["access_token"]


# -------------------------------------------------------
# First-time OAuth flow (run once manually)
# -------------------------------------------------------
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

def run_initial_auth(open_browser=True):
    """
    Opens the browser for the TikTok authorization.
    Starts a local HTTP server to capture the callback code.
    Uses PKCE (S256) as required by TikTok v2 API.
    """
    captured = {}
    code_verifier, code_challenge = _generate_pkce()

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

    server = None
    try:
        server = http.server.HTTPServer(("localhost", 8080), CallbackHandler)
        server_thread = threading.Thread(target=server.handle_request, daemon=True)
        server_thread.start()
    except Exception as e:
        print(f"[TikTok] Local port 8080 listener skipped: {e}")

    state = f"tiktok_{secrets.token_hex(6)}"
    auth_url = (
        f"{AUTH_BASE}?"
        + urllib.parse.urlencode({
            "client_key":            CLIENT_KEY,
            "response_type":         "code",
            "scope":                 SCOPES,
            "redirect_uri":          REDIRECT_URI,
            "state":                 state,
            "code_challenge":        code_challenge,
            "code_challenge_method": "S256",
        })
    )

    global LAST_AUTH_URL
    LAST_AUTH_URL = auth_url
    
    if open_browser:
        print(f"[TikTok] Opening browser for authorization...")
        _open_browser(auth_url)
    else:
        print(f"[TikTok] Skipping backend browser open, URL will be returned to client.")

    print("[TikTok] Waiting for callback (max 120s)...")
    start_time = time.time()
    while time.time() - start_time < 120:
        if "code" in captured:
            break
        try:
            poll_resp = requests.get(
                f"https://api.meloscribe.dev/api/oauth/code?state={state}",
                timeout=3
            )
            if poll_resp.status_code == 200:
                data = poll_resp.json()
                if data.get("status") == "ok" and data.get("code"):
                    captured["code"] = data["code"]
                    print(f"[TikTok] Code received via api.meloscribe.dev relay!")
                    break
        except Exception:
            pass
        time.sleep(1.5)

    if server:
        try:
            server.server_close()
        except Exception:
            pass

    if "code" not in captured:
        print("[TikTok] ERROR: No authorization code received within timeout.")
        return False

    code = captured["code"]
    print(f"[TikTok] Code received: {code[:12]}...")

    # Exchange code for tokens — must include code_verifier for PKCE
    resp = requests.post(TOKEN_URL, data={
        "client_key":     CLIENT_KEY,
        "client_secret":  CLIENT_SECRET,
        "code":           code,
        "grant_type":     "authorization_code",
        "redirect_uri":   REDIRECT_URI,
        "code_verifier":  code_verifier,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})

    if resp.status_code != 200:
        print(f"[TikTok] Token exchange failed: {resp.status_code} — {resp.text}")
        return False

    data = resp.json()
    _save_tokens({
        "access_token":  data["access_token"],
        "refresh_token": data["refresh_token"],
        "open_id":       data["open_id"],
        "expires_at":    time.time() + data.get("expires_in", 86400),
    })

    print("[TikTok] Authorization complete! Tokens saved.")
    return True


if __name__ == "__main__":
    print("=== TikTok First-Time Authorization ===")
    if TOKENS_PATH.exists():
        print(f"Tokens already exist at {TOKENS_PATH}.")
        print("To re-authorize, delete tiktok_tokens.json and run again.")
    else:
        run_initial_auth()
