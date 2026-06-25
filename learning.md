# Meloscribe Learning & Bug Ledger

Living database tracking technical quirks, bugs, solutions, and environment lessons.

## Learnings & Environment Insights

### 2026-06-11: New PC Migration & Steam Paths
- **Issue**: Transitioning to a new PC left several hardcoded `D:\Antigravity Music` paths in ruins.
- **Lesson**: Steam on the new PC is installed on the `C:` drive in the standard location (`C:\Program Files (x86)\Steam`). Keysight's executable lies there instead of `D:\SteamLibrary`.
- **Resolution**: Implemented dynamic configuration loading across all sub-scripts (`keysight_bot.py`, `cover_generator.py`, `kofi_zipper.py`) leveraging the backend `settings.py` so paths can be changed on-the-fly, falling back to verified `C:\` locations.

### 2026-06-11: Redundant FastAPI Routes
- **Issue**: Two `/api/settings` routes existed in `main.py`. The top route utilized a custom Pydantic parser that discarded directory paths, whereas the lower route used the flexible `settings.py` module. Because FastAPI resolves routes sequentially, the top route shadowed the lower one, causing settings (especially paths) to get wiped or ignored on save.
- **Resolution**: Removed the redundant Pydantic model and old settings handlers, allowing the fully featured `settings.py` integration to serve `/api/settings`.

### 2026-06-11: MuseScore Template & Score Path Automation
- **Issue**: MuseScore template files were prone to accidental overwriting if opened directly. Additionally, score files were scattered under global documents rather than tracked inside the project workspace directory.
- **Resolution**: Updated configurations to use `C:\Dev\meloscribe\Scores` as the workspace-centric scores directory. Integrated automatic copy-renaming of the template to target `{song}.mscz` on the fly, launching two instances (MIDI for reference and copied template for sheet music creation) in `kofi_zipper.py` dynamically. Added MuseScore EXE configurability in settings and React UI.

### 2026-06-18: MuseScore Startup Race Conditions & SSH Key Validation
- **Issue**: Launching two MuseScore instances (MIDI and Score copy) simultaneously causes race conditions where one window sometimes fails to load the file and starts blank. Additionally, running the Oracle Sniper status check script (`check_sniper.bat`) fails silently or blocks if the SSH identity file is missing or on a new PC requiring host key verification.
- **Resolution**: Added a `time.sleep(1.5)` delay in [kofi_zipper.py](file:///c:/Dev/meloscribe/tools/kofi_zipper.py) between MuseScore process spawns to resolve race conditions. Added validation to [check_sniper.bat](file:///C:/Users/Ventoba/Desktop/check_sniper.bat) to verify the key exists first and output a warning if it doesn't. Optimized the SSH command with `-o StrictHostKeyChecking=accept-new` and `-o ConnectTimeout=5` to prevent terminal hangs on new machines. Corrected the key file's NTFS permissions via PowerShell (limiting access solely to the current user) to satisfy the OpenSSH client requirements, and fixed the Linux command syntax (changing `echo.` to `echo`) to successfully display the recent logs.

### 2026-06-18: Missing API Tokens & Hidden Directory Backups
- **Issue**: Social media API tokens (`yt_tokens.json`, `ig_tokens.json`, etc.) were missing after PC migration. The user hoped to retrieve them from old chat history logs on the backup drive `X:\`.
- **Lesson**: Standard AppData backups and custom user folder copies often omit hidden directories starting with a dot (like `.gemini` in `%USERPROFILE%\`) if not explicitly selected. Consequently, the old chat history logs containing the tokens are not present on the backup drive.
- **Resolution**: Conducted a thorough search across all SQL databases (`state.vscdb`), zip archives, AppData directories, and desktop files on `X:\Backups`. Concluded that the tokens are not in the backups. Triggered next actions to re-authenticate the API accounts via the Electron frontend's Settings tab.

### 2026-06-18: ngrok Tunnel offline (ERR_NGROK_3200) & stdout encoding crash
- **Issue**: The ngrok endpoint `wooing-encrust-ladle.ngrok-free.dev` was reported offline with error `ERR_NGROK_3200`. Additionally, the backend was failing to initialize the tunnel due to an encoding crash.
- **Lesson**: 
  1. On Windows, ngrok by default resolves the port "8787" via `localhost:8787` which maps to IPv6 `::1`. Since Uvicorn was configured to run on IPv4, ngrok failed to connect to Uvicorn, causing `ERR_NGROK_3200`.
  2. Windows console interfaces default to CP1252 (charmap). Python's default buffered/unbuffered output will crash with a `UnicodeEncodeError` when trying to print non-ASCII symbols like `→` (`\u2192`) when redirected or piped.
- **Resolution**:
  1. Downloaded and extracted the complete `ngrok.exe` binary to `tools/ngrok/ngrok.exe`.
  2. Modified the ngrok startup command in [main.py](file:///c:/Dev/meloscribe/tools/meloscribe/backend/main.py) to explicitly target `127.0.0.1:8787` (IPv4) instead of `8787`.
  3. Replaced the Unicode arrow `→` with the ASCII equivalent `->` in the print statement of `main.py` to prevent encoding crashes.

### 2026-06-18: PowerShell Out-File creates UTF-8-with-BOM — breaks Python JSON parsing
- **Issue**: When using `$obj | Out-File -Encoding utf8` in PowerShell 5.x (the default on Windows), the resulting file gets a UTF-8 BOM (Byte Order Mark) prepended. Python's `json.load()` cannot parse BOM-prefixed files and throws `JSONDecodeError: Expecting value at char 0`.
- **Resolution**: Always write credential/token JSON files using the dedicated file write tool (no BOM) instead of PowerShell's `Out-File`. If a user writes a file via PowerShell, read the raw content back and rewrite it cleanly using the file tool. Alternatively, instruct users to use `Out-File -Encoding utf8NoBOM` (PowerShell 6+) or `[System.IO.File]::WriteAllText()`.

### 2026-06-18: Ko-Fi cookie names are custom, not standard ASP.NET names
- **Issue**: Assumed the Ko-Fi auth cookie was named `.AspNetCore.Identity.Application` (the ASP.NET Core default), but the actual cookie names are `kofi_identity_cookie` and `kofiweb.session`.
- **Lesson**: Never guess cookie names. Always verify via browser DevTools (F12 → Application → Cookies → domain). Ko-Fi uses custom names that differ from ASP.NET Core defaults.
- **Resolution**: Updated `kofi_cookie.txt` to use the correct names `kofi_identity_cookie` and `kofiweb.session`. CSV sync then worked immediately and imported 16 historical sales into `analytics.db`.

### 2026-06-21: Electron Binary Extraction & VBS Script Launcher Issues
- **Issue**:
  1. Node.js v26.3.0 fails to extract Electron binaries using the `extract-zip` library, exiting silently or failing to create `path.txt` in `node_modules/electron`, resulting in `Error: Electron failed to install correctly`.
  2. Double-clicking the Desktop shortcut did nothing because the shortcut target used `wscript.exe` executing `launch_silent.vbs`. Running `.vbs` files via WScript was blocked by Windows Script Host settings on the host machine, causing the shortcut creation and silent launcher to fail silently.
- **Resolution**:
  1. Manually extracted the Electron zip archive (`electron-v35.7.5-win32-x64.zip` from cache) into `node_modules/electron/dist` using PowerShell's `Expand-Archive` cmdlet, and wrote `electron.exe` to `path.txt`.
  2. Replaced the VBS shortcut generator with a native PowerShell script `create_shortcut.ps1` that instantiates the Shell COM object. The script generates the Desktop shortcut pointing directly to `start_meloscribe.bat` with `WindowStyle = 7` (runs minimized on the taskbar), bypassing WScript and the silent VBS wrapper completely. Modified `main.py` to run this script on startup via `powershell.exe`.

### 2026-06-21: Dynamic OCI Queue Filename Resolution & Combined SSH Invocation
- **Issue**: The staged queue list needs to display exact filenames uploaded to the server, and tasks need to be rescheduled or deleted. Performing separate SSH queries for each item would introduce significant connection latency.
- **Lesson**: Standardizing staging file locations allows mapping file patterns using metadata. Combined commands (`python3 -c "..." && find ...`) can retrieve both structured queue data and the staged folder structure in a single SSH invocation, drastically reducing network overhead.
- **Resolution**: Updated the queue GET endpoint in `main.py` to run a combined Python SQL dump and file tree search. Parsed relative file paths under `staging/` inside FastAPI, dynamically matching Ko-Fi zips and social media portrait/landscape videos by their prefix and suffix (e.g. `slow` vs. normal). Added direct SQL SSH action wrappers for DELETE and UPDATE queries, exposing them in the React UI with inline and modal inputs.

### 2026-06-21: Systemd Journalctl Hangs & Native Log Redirection via Service Configuration
- **Issue**: Standard `systemctl status` and `journalctl` commands were hanging on an Oracle OCI Free-Tier server during remote SSH queries due to large journal logs or PolicyKit waiting blocks. This triggered API timeouts in the local FastAPI application.
- **Lesson**: 
  1. Lightweight checks like `systemctl is-active` resolve instantly and do not scan logs or use pagers.
  2. Directing service stdout to a local file using `StandardOutput=append:/path/to/log` enables fast and reliable log access via simple file reads (`tail`), avoiding the journal database search overhead entirely.
- **Resolution**: Replaced status calls in `main.py` with `systemctl is-active`. Modified the server-side uploader service to use `StandardOutput=append` and `StandardError=append` logs in `/home/ubuntu/meloscribe/uploader.log`. Updated logs querying in `main.py` to use fast `tail` commands directly on log files instead of `journalctl`.

### 2026-06-22: One-click OAuth Flows for Instagram/Threads
- **Issue**: Standard access tokens for Meta platforms (Instagram Graph API, Threads API) are short-lived or long-lived but expire if inactive, password changes, or session expires. Manually generating tokens from the Facebook Graph API Explorer is tedious.
- **Lesson**: Standardizing state query routing (like `state=fb` and `state=threads`) through a unified ngrok/FastAPI redirect proxy (`/callback`) allows reusing a single temporary localhost server (`localhost:8080`) to dynamically capture codes across different social media platforms.
- **Resolution**: Created `ig_auth.py` and `threads_auth.py` to orchestrate browser opening, capture callback codes on `localhost:8080`, and perform token exchanges. Integrated these scripts into `main.py` endpoints `/api/instagram/authorize` and `/api/threads/authorize` when called without a payload, and added corresponding UI buttons to the Settings tab with manual fields preserved as fallbacks.

### 2026-06-22: Browser Window fails to open from background subprocess
- **Issue**: Standard Python `webbrowser.open()` calls fail to open a browser window on the active desktop when executed inside background service threads or from subprocesses spawned by Electron.
- **Lesson**: Desktop app configurations should store the explicit path to the browser executable (e.g. Brave/Chrome). Directly executing `subprocess.Popen([browser_path, url])` bypasses desktop environment session limitations and guarantees the browser opens in the user's active session.
- **Resolution**: Updated `tiktok_auth.py`, `ig_auth.py`, `threads_auth.py`, and `yt_auth.py` to prioritize `os.startfile(url)` which leverages the native Windows Explorer shell, cleanly opening new tabs in already-running browser instances. Spawning processes via `browser_exec` and fallback to `webbrowser` are kept as backups.

### 2026-06-22: FastAPI POST Requests without Request Body raise 422 Unprocessable Content
- **Issue**: When a frontend POST request is sent without a payload to an endpoint expecting a Pydantic model parameter (e.g., `POST /api/threads/authorize`), FastAPI/Pydantic throws a `422 Unprocessable Content` validation error before the endpoint handler is run.
- **Lesson**: 
  1. FastAPI requires body model parameters to be declared as optional with default values (e.g., `req: Optional[Model] = None`) to accept payload-free POST requests.
  2. The client should proactively send a valid empty JSON payload (`{}`) and correct `Content-Type: application/json` headers when calling endpoints expecting JSON schemas.
- **Resolution**: Updated the FastAPI signatures for Instagram and Threads authorize routes in `main.py` with optional defaults, added check checks to avoid dereferencing `None`, modified the frontend's `handleOAuth` method to send empty JSON objects, and recompiled the frontend.

### 2026-06-22: Meta OAuth Domains and Redirect URIs Configuration
- **Issue**: During browser OAuth logins, Facebook/Instagram throws a "URL cannot be loaded: The domain of this URL is not included in the app's domains" error.
- **Lesson**: Meta requires the exact redirect domain (e.g. the active ngrok sub-domain `wooing-encrust-ladle.ngrok-free.dev`) to be configured in the Meta Developer Console under:
  1. **App Settings > Basic > App Domains**
  2. **Facebook Login > Settings > Valid OAuth Redirect URIs** (e.g. `https://wooing-encrust-ladle.ngrok-free.dev/callback`).
- **Resolution**: Whitelist the active ngrok domain in the Meta Developer Dashboard, or utilize the manual fallback input to exchange tokens directly.

### 2026-06-22: Threads API Requires Dedicated Threads App ID
- **Issue**: Standard authorization attempts on Threads return error `4476002: In der Anfrage wurde keine App-ID übermittelt.`
- **Lesson**: The Threads API requires a separate **Threads App ID** and **Threads App Secret** found at the bottom of the **App Settings > Basic** tab in the Meta Developer Console, which is distinct from the primary Meta App ID shown at the top of the page.
- **Resolution**: Enabled `threads_auth.py` and `ig_auth.py` to dynamically load `threads_app_id` and `threads_app_secret` from `settings.json` if available, falling back to defaults. Added manual token submission as a robust alternative.### 2026-06-25: Oracle VM missing packages & Git Credentials block
- **Issue**: 
  1. The Oracle VM lacked `git`, `nginx`, `certbot`, and `sqlite3` packages, which prevented standard deployment workflows.
  2. Running `git push` or `git pull` on HTTPS remotes without credentials cached will block indefinitely waiting for console user/pass inputs in background tasks.
- **Lesson**: 
  1. Do not assume infrastructure tools are present. Validate and install via `apt-get` if missing.
  2. For automated backend deploys or first-time setup, use SCP to bypass credential blocks, and instruct the user to configure credentials (e.g. personal access tokens or SSH deploy keys) directly for future pulls.
- **Resolution**: Installed the packages on the server. Initialized the Server Git repository linked to `https://github.com/Ventoba/meloscribe.git`, completed the deployment via manual SCP file sync, migrated the SQLite DB, and restarted the backend service.
