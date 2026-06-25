import os
from playwright.sync_api import sync_playwright
import time

user_data_dir = os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\User Data")
executable_path = os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe")
if not os.path.exists(executable_path):
    executable_path = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        executable_path=executable_path,
        headless=False,
        ignore_default_args=["--enable-automation"],
        args=["--profile-directory=Default", "--window-size=1200,900"],
        no_viewport=True
    )
    
    page = browser.pages[0]
    print("Navigating to Ko-Fi support received page...")
    page.goto("https://ko-fi.com/manage/supportreceived?src=sidemenu")
    time.sleep(5) # let it load
    
    html = page.content()
    with open("kofi_dom.html", "w", encoding="utf-8") as f:
        f.write(html)
        
    print("Saved DOM to kofi_dom.html")
    browser.close()
