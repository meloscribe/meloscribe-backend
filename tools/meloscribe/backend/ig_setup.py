import os
import json
import requests
from pathlib import Path

APP_ID = "26975285422066567"
APP_SECRET = ""
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
except Exception:
    pass

def setup_instagram_account(short_lived_token: str):
    print("--- Instagram Graph API Setup ---")
    print("1. Exchanging short-lived token for a 60-day long-lived token...")
    
    url = f"https://graph.facebook.com/v19.0/oauth/access_token?grant_type=fb_exchange_token&client_id={APP_ID}&client_secret={APP_SECRET}&fb_exchange_token={short_lived_token}"
    
    try:
        resp = requests.get(url)
        data = resp.json()
        if 'error' in data:
            print(f"[X] Error exchanging token: {data['error']['message']}")
            return False
            
        long_lived_token = data.get('access_token')
        print("[OK] Long-lived token acquired successfully!")
        
        # 2. Find Instagram Business Account ID and NON-EXPIRING Page Token
        print("\n2. Finding connected Instagram Business Account & Permanent Page Token...")
        # We request 'access_token' on the page level, which returns a permanent token when queried with a long-lived user token!
        me_url = f"https://graph.facebook.com/v19.0/me?fields=id,name,accounts{{instagram_business_account,name,access_token}}&access_token={long_lived_token}"
        
        resp_me = requests.get(me_url)
        me_data = resp_me.json()
        
        ig_business_id = None
        page_id = None
        page_name = None
        page_token = None
        
        if 'accounts' in me_data and 'data' in me_data['accounts']:
            for page in me_data['accounts']['data']:
                if 'instagram_business_account' in page:
                    ig_business_id = page['instagram_business_account']['id']
                    page_id = page['id']
                    page_name = page.get('name', 'Unknown')
                    page_token = page.get('access_token')
                    break
                    
        if not ig_business_id or not page_token:
            print("[X] No Instagram Business Account or Page Token found linked to the Facebook Pages this token can access.")
            return False
            
        print(f"[OK] Found Instagram Business Account (ID: {ig_business_id}) linked to Facebook Page '{page_name}' (ID: {page_id})")
        print("[OK] Acquired PERMANENT Page Access Token! You won't have to re-authenticate every 60 days.")
        
        # 3. Save to ig_tokens.json
        save_data = {
            "access_token": page_token, # Save the permanent page token, not the 60-day user token!
            "ig_business_id": ig_business_id,
            "fb_page_id": page_id,
            "fb_page_name": page_name
        }
        
        with open(TOKENS_PATH, "w") as f:
            json.dump(save_data, f, indent=4)
            
        print(f"\n[OK] Setup Complete! Credentials saved to {TOKENS_PATH.name}")
        return True
        
    except Exception as e:
        print(f"[X] Critical setup error: {e}")
        return False

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        SHORT_TOKEN = sys.argv[1]
    else:
        SHORT_TOKEN = input("Please enter your Facebook Graph short-lived user access token: ").strip()
    if SHORT_TOKEN:
        setup_instagram_account(SHORT_TOKEN)
    else:
        print("[X] Token is required.")
