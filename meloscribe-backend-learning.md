# meloscribe-backend — Learning & Bug Ledger

Living database of technical quirks, bugs, environment insights, and resolved issues for the Meloscribe backend (`C:\Dev\meloscribe-backend`).

### 2026-10-22: Facebook +4 Hours Scheduling Offset & Auto-Scheduling Cadence Drift Prevention
- **Facebook Platform Schedule Staggering & Server Queue Calibration (`stage_to_server.py`, `upload_bot.py`, `queue.db`)**:
  - *Context:* The user established a strict requirement that all Facebook video releases must publish +4 hours later than the standard release time (e.g. 16:00 CEST releases publish on Facebook at 20:00 CEST), while keeping other platforms (YouTube, Instagram, TikTok, Threads) on their base schedule.
  - *Root Cause Analysis & Potential Cadence Drift:*
    1. In `stage_to_server.py`, all social media platforms received identical timestamps (`dates['original_normal']`, `dates['original_tutorial']`, etc.).
    2. Merely shifting Facebook tasks inside `queue.db` without adjusting `get_base_schedule_time()` would cause subtle time drift: `SELECT max(schedule_time) FROM upload_queue` would return Facebook's 20:00 timestamp (or Pinterest's 21:00 timestamp) as `max_pending`, causing the next song's base time to increment to 20:00 instead of 16:00, cascading into midnight releases.
  - *Resolution:*
    1. In `stage_to_server.py` (`register_task`), added an automated +4-hour delta when `mode == "facebook"`, correctly adjusting date and time across both standard releases and content recycling staging.
    2. In `get_base_schedule_time()`, filtered out background Pinterest pins (`mode != 'pinterest'`) and locked `spaced_dt` to `req_dt.time()` (`datetime.datetime.combine((max_dt + timedelta(days=interval_days)).date(), req_dt.time())`), guaranteeing future staged songs always preserve their intended release hour (e.g. 16:00 CEST).
    3. In `upload_bot.py`, added a defensive +4-hour offset to `args.datetime` for direct CLI/scheduler runs.
    4. Synchronized changes across `meloscribe-app`, `meloscribe-backend`, and the live production Oracle VM (`/home/ubuntu/meloscribe/tools/upload_bot.py`).
    5. Updated the upcoming pending Facebook release in `queue.db` on Oracle VM (Task 210: *Experience Easy (Tutorial)*) from `2026-10-24 16:00` to `2026-10-24 20:00`.

### 2026-10-21: Pinterest SEO Description Optimization & Link Redundancy Removal
- **Pinterest Description Redundant Links & SEO Keyword Strategy (`pinterest_poster.py`, `settings.json`, `settings.py`)**:
  - *Context:* The user identified that scheduled and published Pinterest posts contained the full raw song link in the description text. On Pinterest, description text URLs are non-clickable plain text, look spammy, and take up character count that should be used for search ranking keywords. Furthermore, Pinterest posts already have the native clickable destination button/link configured via `pin_data["link"] = song_link`.
  - *Root Cause:*
    1. In `pinterest_poster.py`, the hardcoded default description generator inserted `{song_link}` right in the body: `✨ Sheet Music (PDF), MIDI & Tutorial:\n{song_link}`.
    2. In `settings.json` and `settings.py`, `desc_template_pinterest` included `{song_link}` at the end of the hashtags.
  - *Resolution:*
    1. In `pinterest_poster.py`, removed `{song_link}` completely from the description text.
    2. Replaced the unclickable link with a high-converting CTA directing viewers to the native clickable link on the Pin: `✨ Click the Pin to get the Sheet Music (PDF), MIDI & practice tutorial!`.
    3. Enriched the description with high-ranking Pinterest SEO hashtags and search keywords: `#{clean_tag}`, `#{author_tag}`, `#easypiano`, `#learnpiano`, `#pianomusic`, `#aestheticpiano`, `#pianonotes`, `#solopiano`, `#pianopractice`.
    4. Cleaned `{song_link}` from `settings.json` and `settings.py` across `meloscribe-app` and `meloscribe-backend`.
    5. Deployed the updated `pinterest_poster.py` and `settings.json` to Oracle Cloud production VM (`152.70.23.171`) via SCP. All pending scheduled tasks (`I don't know`, `I don't know Easy`, `Je te laisserai des mots`, `Icarus`) dynamically generate descriptions at upload time and will immediately publish with the optimized copy.
  - *Pinterest Video Transcode Polling Timeout & Clean Cover Image Fallback*:
    - *Context:* The user noticed that certain Pinterest posts (such as *Golden Brown Easy* and *Sweetest Rain*) were published as static image Pins instead of video Pins.
    - *Root Cause:* In `pinterest_poster.py`, `upload_video_media()` had a strict `max_wait_seconds = 180` (3 minutes) polling limit for Pinterest's server-side video transcoding (`GET /v5/media/{media_id}`). Heavy 1440x2560 60fps video files (e.g., *Golden Brown Easy* at 370.1 MB) or periods of high Pinterest server transcode load (e.g., *Sweetest Rain*) exceeded 180 seconds. When the timeout expired, `upload_video_media()` returned `None`, triggering the built-in safety fallback in `post_pin()`: `media_source = {"source_type": "image_url", "url": cover_url}`. Rather than failing the upload task, the bot safely published the clean cover art as an image Pin. Direct inspection of the Pinterest API confirmed both media IDs (`3959571195591309056` and `3961988367920086336`) did eventually succeed transcoding (`status: 'succeeded'`) shortly after the 180s cutoff.
    - *Resolution:* Increased `max_wait_seconds` from 180s to **420s (7 minutes)** in `pinterest_poster.py` across `meloscribe-app`, `meloscribe-backend`, and Oracle VM (`152.70.23.171`), providing sufficient headroom for large files and high Pinterest transcode queue latency without prematurely triggering the image fallback.

  - *Pinterest Video Pin Cover Requirement & Native Frame Extraction (`cover_image_key_frame_time`)*:
    - *Context:* The user noticed that published Pinterest video Pins displayed an empty background image (clouds + empty keyboard without song title or falling notes), and only upon hover did the actual video with title and notes play.
    - *Initial Regression & Root Cause:* In `pinterest_poster.py`, `post_pin()` previously passed `"cover_image_url": f"https://meloscribesheets.com/covers/{clean_song}_clean.jpg"` inside the `media_source` object. Removing `cover_image_url` entirely caused Pinterest API v5 to reject the upload with HTTP 400: `{"code":1,"message":"Please provide either cover_image_url or cover_image_content_type and cover_image_data or cover_image_key_frame_time"}`.
    - *Resolution:* Pinterest API v5 strictly mandates at least one cover specification method for video Pins. Instead of passing an external static image URL or uploading raw image bytes, pass `"cover_image_key_frame_time": 2` (must be an integer, whole seconds from video start). At second 2, Meloscribe videos show the full song title, author typography, and falling notes. Pinterest natively grabs and renders this frame as the Pin's card thumbnail across all feeds, with zero external cover art or hover mismatch.
    - *Files Updated:* `pinterest_poster.py` in `meloscribe-app`, `meloscribe-backend`, and `/home/ubuntu/meloscribe/tools/meloscribe/backend/pinterest_poster.py` on Oracle VM (`152.70.23.171`). Rescheduled all failed tasks (Tasks 233–236) which published successfully.

### 2026-10-18: TikTok Safe Area Portrait Title Typography & Smart 2-Line Centering Architecture
- **In-Video Portrait Title Position & TikTok Safe Area Clearance (`video_generator.py`)**:
  - *Context:* The user noticed in TikTok video screenshots that single-line titles like *Experience* were positioned uncomfortably high up (`title_y = 390`), hugging the search bar header (`y ≈ 0..340`) while leaving a large empty gap before the subtitle and piano keys.
  - *Root Cause:*
    1. Previous hardcoded vertical positioning placed single-line titles at `y = 390` (and `y = 440` without subtitle). On modern mobile devices (iPhone Dynamic Island, Android notch, plus TikTok/Reels search navigation), the top UI covers down to `y = 340..350`. This left only ~40px breathing room, looking crowded.
    2. In addition, `title_text = re.sub(r'\s+', ' ', title_text)` stripped all explicit newlines (`\n`), preventing intentional multiline formatting.
    3. Long titles (e.g. *Welcome to the Black Parade*, *Comptine d'un autre été: L'Après-Midi*) exceeded the 1440px canvas width (>1490px at fontsize 130).
    4. Crucially, in FFmpeg, putting multiline text into a single `drawtext` filter with `x=(w-text_w)/2` computes text width using the widest line and **left-aligns** all subsequent lines to line 1's X coordinate!
  - *Resolution:*
    1. **Dynamic `y_overlay`-Relative Positioning Across All Zooms**:
       - Instead of fixed absolute coordinates, both Title and Subtitle are dynamically anchored to `y_overlay` (the moving top edge of the cropped video canvas):
         - 1-Line: `author_y = y_overlay - 105`, `title_y = author_y - 125`.
         - 2-Line: `author_y = y_overlay - 95`, `title2_y = author_y - 110`, `title1_y = title2_y - 105`.
       - Ensures the distance from Subtitle to video edge is **always 105px** (or 95px for 2-line), completely eliminating large empty gaps regardless of whether zoom is 1.0, 1.3, 1.45, or 1.55.
    2. **TikTok Safe Area Ceiling Clamping**:
       - `title1_y` and `title_y` clamp at `y >= 365`, ensuring titles never push up into TikTok's top navigation bar (`y ≈ 0..340`) even under heavy crop zoom.
    3. **Smart 2-Line Title Splitting (`split_title_lines`)**:
       - Preserves manual newlines (`\n` or `\\n`).
       - If rendered single-line width exceeds 1080px (measured via `PIL.ImageFont` on `arno_pro.ttf` at fontsize 130), dynamically tests all word boundaries to balance the pixel widths of Line 1 and Line 2.
    4. **Dual-Layer FFmpeg Horizontal Centering**:
       - 2-line titles write `title1_txt` and `title2_txt` separately and chain two independent `drawtext` layers in the filter complex, each with its own `x=(w-text_w)/2`.
       - Fontsize scales to `108px` (Line 1 `title1_y`, Line 2 `title2_y`, Subtitle `author_y`, `subtitle_fontsize = 60`).
       - Both lines are individually and perfectly centered on screen.
    5. Synchronized across `meloscribe-app` and `meloscribe-backend`.

### 2026-10-18: In-Video CTA Overlay Timing Architecture & Stripe Live Environment Default Sync
- **In-Video CTA Overlay Disappearance & Outro Re-Appearance (`video_generator.py`)**:
  - *Context:* The user noted that the bottom CTA text overlay (`Sheets in bio →` / `Want to play this?`) persisted on screen all the way until the end of the video, obscuring the performance area.
  - *Root Cause:* In commit `a4154e1`, a third `drawtext` layer was re-introduced with `alpha='if(lt(t,9.3),0,if(lt(t,9.8),(t-9.3)/0.5,1))'` (or `alpha='if(lt(t,3),0,min(1,(t-3)/0.5))'` without morph text). This set alpha permanently to `1.0` through the remainder of the clip until the final outro fade.
  - *Resolution:*
    1. Redesigned portrait mode CTA timing in `tools/video_generator.py`:
       - With `morph_text`: CTA appears at 3.0s, morph text appears at 6.3s, fades out cleanly by 9.3s, leaving the piano keys and stage empty.
       - Without `morph_text`: CTA appears at 3.0s, fades out smoothly between 6.0s and 6.5s.
       - Re-appearance: If the video is long enough (`fade_start - 3.5 >= 12.0s` with morph, or `>= 9.0s` without), the CTA reappears for ~3.5s right before `fade_start`, naturally dissolving as the outro slate arrives.
    2. Synchronized across `meloscribe-app`, `meloscribe-backend`, and Oracle VM (`152.70.23.171`).
- **Stripe Environment Badge Default Alignment (`settings.py`, `settings.json`, `App.jsx`)**:
  - *Context:* The desktop app unexpectedly displayed the yellow "sandbox" badge in the header despite live orders and webhooks processing successfully on Stripe.
  - *Root Cause:* `DEFAULT_SETTINGS` in `settings.py` and `settings.json` lacked an explicit `"environment": "live"` key. When the desktop frontend initialized, React `useState` defaulted to `'sandbox'` until manually toggled.
  - *Resolution:* Added `"environment": "live"` to `DEFAULT_SETTINGS`, `settings.json`, and set `'live'` as default fallback in `App.jsx`. Synced to Oracle Cloud VM and confirmed `/api/settings` response.
- **Newsletter Subscribers Status Grouping & Hierarchy (`routes_admin.py`, `WebsiteTab.jsx`)**:
  - *Context:* In the desktop app's Website/Newsletter tab, subscribers were ordered strictly by `created_at DESC`. Confirmed active subscribers were interleaved with pending DOI leads.
  - *Resolution:*
    1. Updated SQL query in `routes_admin.py` to prioritize `CASE WHEN status = 'active' THEN 0 WHEN status = 'pending' THEN 1 ELSE 2 END ASC`, followed by `COALESCE(confirmed_at, created_at) DESC`.
    2. Added `useMemo` in `WebsiteTab.jsx` for client-side hierarchical sorting and upgraded active badge to `badge--success` (green).
    3. Added a clean visual section divider in the table demarcating pending double opt-in leads.
- **Community Suggestions Completed Lifecycle & Open/Done Filtering (`routes_public.py`, `AiAdvisor.jsx`)**:
  - *Context:* The user noticed that songs already arranged and completed (e.g. *Experience*, *River Flows in You*, *Sweetest Rain* / *Sweet rain*) were removed from open suggestions on the website, but continued to appear in the desktop app's suggestions card with "+ Todo" buttons.
  - *Root Cause:*
    1. On the website (`Suggestions.tsx`), suggestions matching catalog songs or marked `status === 'completed'` are automatically routed to the "Already Arranged" tab and excluded from open voting.
    2. However, `/api/public/suggestions` returned all database rows unfiltered.
    3. In `AiAdvisor.jsx`, suggestions were rendered without checking `sug.status !== 'completed'`.
  - *Resolution:*
    1. Added `status` query parameter support in `routes_public.py` (`open`, `completed`, or all).
    2. Windows proxy sync now prunes deleted remote suggestions from local SQLite.
    3. In `AiAdvisor.jsx`, separated suggestions into `Open` (default) and `Done` tabs. Open wishlist only displays actionable, unfulfilled requests. Completed requests display a green "Done" badge.
    4. Deployed to Oracle VM (`152.70.23.171`) and rebuilt desktop app bundle.



### 2026-10-12: Universal Concert Stage Floor Reflection & Retirement of Static Addon Overlays
- **Universal Concert Stage Floor Keyboard Reflection (`video_generator.py`)**:
  - *Context:* Previously, the dynamic piano floor reflection was isolated in an `else:` branch of `if args.use_portrait_addon:`. The automation pipeline (`routes_workflow.py`, `upload_bot.py`, `settings.json`) passed `--use_portrait_addon` by default, which loaded static theme textures (e.g. `warm addon.png` for Experience) and bypassed reflection completely. Only videos rendered without the flag (such as "Die drei ???") received the reflection.
  - *Resolution:*
    1. Removed static `addon.png` image loading and overlay branches from `video_generator.py`.
    2. Made the glossy concert stage reflection (with satin diffusion `boxblur=10:2`, peak alpha 65, smoothstep 18px contact ramp) the default behavior across all color themes (Warm, Cold, Green, Violet, Platinum).
    3. Retained `--no_reflection` flag for optional deactivation and marked `--use_portrait_addon` as deprecated.
    4. Updated desktop UI in `MasterTab.jsx` (`enableReflection: 'piano stage reflection'`) and `routes_workflow.py`.
    5. Removed obsolete static `* addon.png` and `* addon.jpg` files from `tools/`.
    6. Synchronized across `meloscribe-app` and `meloscribe-backend`.

### 2026-10-12: SCP Staging Idempotency & YouTube Scheduling Architecture
- **SCP Staging Idempotency & Remote Size Checking (`stage_to_server.py`)**:
  - *Context:* When resuming an interrupted staging upload, `stage_to_server.py` previously re-uploaded all files from index 0 sequentially, taking over an hour on large collections.
  - *Resolution:* Added pre-upload check using `find {SERVER_DIR}/staging/{song}/TikToks {SERVER_DIR}/staging/{song}/Covers -type f -printf '%s %p\\n'`. Compares exact local byte sizes against remote files and skips identical files in <1 second. Replaced fragile `stat` glob syntax which threw errors on empty directories.
- **YouTube Native Scheduling vs Oracle VM Staging Architecture**:
  - *Finding:* YouTube Data API v3 (`publishAt`) returns permanent URLs immediately upon upload. However, YouTube strictly forbids replacing video streams for existing video IDs. Any re-render or edit requires re-uploading, generating a new URL and breaking ArrangeMe external links. Retaining Oracle VM staging preserves full editability of queued video files and schedules prior to broadcast.

### 2026-10-02: TikTok API Direct Post Public Visibility & Redundant ntfy Elimination
- **Redundant ntfy Notification on Native API Posting (`upload_bot.py`)**:
  - *Context:* Prior to TikTok API integration, `upload_bot.py` sent the video caption, hashtags, and cover image to the user's phone via ntfy (`https://ntfy.sh/{ntfy_topic}`) so the creator could paste the description and publish manually in the TikTok mobile app.
  - *Symptom:* When native TikTok API upload was introduced, the `send_ntfy_notification()` call inside `run_tiktok()` was kept in place right before `tiktok_poster.post_video()`. Even though the backend API uploaded the video automatically, the phone continued to receive manual posting pushes.
  - *Resolution:* Removed `send_ntfy_notification()` from `run_tiktok()`. Native API uploads now post captions, hashtags, and authentic frame-0 covers directly without spamming redundant mobile push notifications.
- **TikTok API Direct Post Default Visibility (`PUBLIC_TO_EVERYONE`)**:
  - *Context:* During initial Direct Post testing, uploads were intentionally restricted to `privacy="SELF_ONLY"` (Private / Nur ich) via guard checks in `tiktok_poster.py` to prevent accidental public publishing of test clips.
  - *Symptom:* Scheduled queue jobs (e.g. Task 191 on 2026-10-02) uploaded as private videos, requiring manual publishing on mobile.
  - *Resolution:*
    1. In `upload_bot.py`, defaulted `run_tiktok` to `privacy="PUBLIC_TO_EVERYONE"`, added `--tiktok_privacy` CLI argument, and updated terminal reporting.
    2. In `tiktok_poster.py`, defaulted `privacy="PUBLIC_TO_EVERYONE"`, allowed all valid TikTok privacy levels (`PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `SELF_ONLY`), and updated Direct Post request headers.
    3. Verified live creator API capabilities via `/v2/post/publish/creator_info/query/` (`privacy_level_options: ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]`).
    4. Synchronized patched files across `meloscribe-app`, `meloscribe-backend`, and the live production Oracle VM (`/home/ubuntu/meloscribe/tools/upload_bot.py` and `tiktok_poster.py`). Future queued tasks (such as Task 192 on 2026-10-05) now publish publicly automatically.

### 2026-09-29: YouTube Caption/Title Formatting & Video Highlight Thumbnail Detection Across Social Platforms
- **YouTube Title/Caption Formatting**:
  - *Root Cause:* In `upload_bot.py`, `label` appended `" Tutorial"` when `args.profile == "tutorial"`. The YouTube upload call formatted titles with `f"{base_song} - {args.author}{label} | Piano Cover"`, generating double labels like `In This Shirt - The Irrepressibles Tutorial | Piano Cover`.
  - *Fix:* Decoupled tutorial titles from cover titles:
    - Tutorials: `{base_song} - {author} | Tutorial` (or `(Easy) | Tutorial` if easy version).
    - Piano Covers: `{base_song} - {author} | Piano Cover` (or `Easy | Piano Cover`).
    - Teasers: `{base_song} - {author} (Best Part) | Piano Cover`.
- **Climax / Highlight Video Frame Detection (`extract_video_highlight_frame`)**:
  - Implemented computer-vision scanner in `upload_bot.py` to automatically extract the peak highlight moment from any video (16:9 widescreen or 9:16 vertical):
    - Uses a two-pass algorithm (30 coarse samples across middle 76% of duration, followed by 15 fine samples within ±1.5s of peak).
    - Scores frames by illuminated active keys (HSV value > 180 & saturation > 50 or pure white center > 240) and falling note density.
    - Saves the native full-resolution frame as a high-quality JPEG (`quality=95`).
- **Cross-Platform Social Media Thumbnail Support**:
  - **YouTube**: Full custom thumbnail upload via YouTube Data API v3 (`youtube.thumbnails().set`). Long-form and Shorts upload the highlight frame automatically.
  - **Instagram Reels**: Uses the Graph API's `thumb_offset` parameter. In `ig_poster.py`, replaced the faulty `duration_ms - 100` (which landed in the dark fadeout at the end) with `thumb_offset_ms = int(highlight_ts * 1000)`, locking the Reel cover to the exact highlight climax.
  - **Facebook**: Posts custom thumbnail image to `/{video_id}/thumbnails` with `is_preferred: true` for both long-form videos and Reels.
  - **Threads**: Meta Threads Graph API currently does **NOT** expose any thumbnail upload or `thumb_offset` parameter. Threads handles preview frames automatically in-feed.

### 2026-09-10: Facebook Sync Endpoint Missing & Website Analytics IP-Deduplication Nuance
- **Facebook 0 Views Root Cause**:
  1. Frontend `InsightsTab.jsx` calls `POST /api/facebook/sync` on sync triggers.
  2. The endpoint `@router.post("/api/facebook/sync")` was missing in `routes_settings.py` (returning 404).
  3. Consequently, `fb_sync.py` had not run on the server since May 11, 2026, leaving recent high-performing videos (*Golden Brown* with 229k views, *River Flows in You* with 281k views, *Sweetest Rain* with 82k views) with 0 views in `analytics.db`.
  4. Once endpoint was mounted and `fb_sync.py` executed, all 65 Facebook Page videos populated with their live Graph API view counts.
- **Website Views Counter Nuance**:
  1. The metric labeled "Website Views" in `channel_insights` counted unique client IP addresses per day (`visitor_key = (client_ip, today_str)` via `/api/public/stats`), not total page impressions or clicks.
  2. Repeated song views, sheet clicks, and demo audio listens from the same visitor on a single day incremented this counter 0 times.
  3. Real Nginx server logs reflect >40,000 total HTTP requests and >1,030 confirmed mobile social in-app browser sessions (TikTok: 719, Facebook: 271, etc.).
  4. Centralized all website and checkout funnel intelligence into `WebsiteTab.jsx` under `Checkout & Website Analytics`, keeping `InsightsTab.jsx` 100% focused on social media metrics.

### 2026-08-17: Facebook Graph API Reels (Live Mode Requirement, 90s Duration & Daily Scheduling)
- **Meta App Live Mode vs. Dev Mode Reach**:
  - Uploading Reels via an app in "In Development" mode restricts algorithmic distribution to Page Admins and Testers only (~2-5 views). App must be switched to **Live** mode in `developers.facebook.com` for global Reels For-You / Discovery feed distribution.
- **Facebook Reel 90-Second Cutoff**:
  - `upload_bot.py` uses `is_short = duration <= 90.0` to route vertical videos to `/{page_id}/video_reels` instead of horizontal standard page videos.
- **Hashtag Optimization**:
  - Facebook description templates use proven high-reach tags: `#music #song #piano #cover #cozy #learnpiano #pop #pianotutorial #{song}` without hard links.

---

### 2026-08-16: TikTok Content Posting API v2 (Direct Post vs. Inbox Draft & Chunking Rules)
- **Direct Post (`/v2/post/publish/video/init/`) vs. Inbox Draft (`/v2/post/publish/inbox/video/init/`)**:
  - Direct Post attempts to publish directly to the creator's profile. For sandbox/development apps, it requires either an audited app or a private creator account (otherwise fails with `403 unaudited_client_can_only_post_to_private_accounts`).
  - Inbox Draft (`/v2/post/publish/inbox/video/init/`) sends an in-app actionable notification ("Dein Inhalt ist bereit") to the creator's TikTok app inbox. It works seamlessly for public and private accounts without direct post audit blocks.
- **TikTok API Chunking Integer Math Rule**:
  - `total_chunk_count` MUST BE calculated using integer floor division: `total_chunks = file_size // chunk_size` (NOT `math.ceil`). Sending `math.ceil` causes TikTok API init to reject with `400 invalid_params: The total chunk count is invalid`.
  - For files > 64 MB, use 20 MB chunks (`20 * 1024 * 1024` bytes).
- **TikTok Video Processing Status Pipeline**:
  - After uploading all chunks (where chunk $1..N-1$ return HTTP 206 and chunk $N$ returns HTTP 201), `/v2/post/publish/status/fetch/` initially returns `status: "PROCESSING_UPLOAD"`.
  - TikTok's backend encoder takes ~1 to 3 minutes for large 400 MB files before transitioning to `status: "SEND_TO_USER_INBOX"`, at which point the "Dein Inhalt ist bereit" notification arrives in the TikTok app inbox.

---

### 2026-08-11: NVENC Bitrate Caps, MuseScore 1.4mm Spatium, Process Termination & Test File Locations
- **Seamless Multi-Core Visualizer Particle Pre-calculation**: `audio_visualizer.py` splits rendering across 12 CPU cores. Previously, each worker re-seeded `random.seed` and initialized separate particle arrays per ~20-second chunk (14,161 frames / 12 = ~1,180 frames = 19.6s), causing hard visual cuts every 20 seconds at chunk boundaries. Resolved by globally precalculating `ambient_fx_all` particle trajectories across all frames 0..total_frames sequentially (~0.05s) before worker pool dispatch.
- **Test File Organization Rule**: Temporary test files/videos (e.g., test renders, 5s snippets, crop tests) MUST NEVER be saved inside production directories (`TikToks/`, `Scores/`, `Covers/`). Always save test files to `tools/temp/` or `scratch/`.
- **NVENC Bitrate Cap Rule**: Hardware `h264_nvenc` VBR without `-b:v` causes video bitrates to balloon to 20-25 Mbps (producing 1.1 GB - 1.3 GB files for slow tutorials). Always set `-b:v 5M -maxrate:v 7M` for Portrait (9:16) and `-b:v 6.5M -maxrate:v 9M` for Widescreen (16:9) to keep file sizes within ~120 MB - 360 MB without visual quality loss.
- **TikTok Cover Frame Injection Rule**: Synthetic cover frames/slates (outro slates, 1-frame inserts) have been completely removed from `video_generator.py`. Direct Post uploads use authentic video Frame 0 (`cover_timestamp = 0`), which displays the clean title, notes, and keyboard. Videos render strictly as 100% uninterrupted piano performances.
- **MuseScore 4 Template & Spatium Rule**: Staff space (`<spatium>`) is set to `1.4` mm. Top headers (`evenHeaderL`, `oddHeaderR`, `headerEvenLeft`, `headerOddRight`) MUST be cleared (`""`) so page numbers appear ONLY on bottom-center (`<oddFooterC>$p</oddFooterC>`). `musescore_launcher.py` modifies ALL `.mscx` and `.mss` files in the score archive.
- **Process Cancellation (`/api/workflow/stop`)**: `run_tool` MUST NOT reset `active_workflow_task["stop_requested"] = False` on step entry. `stop_workflow()` uses `psutil` to kill the process tree AND issues `taskkill /F /IM scp.exe /IM ffmpeg.exe` to forcefully terminate detached/orphaned SSH/SCP/FFmpeg sub-processes.
- **Hook MP3 Export Staging**: `upload_bot.py` automatically exports an isolated 192 kbps MP3 of the 9:16 Teaser/Hook video to `C:\Dev\meloscribe\mp3 preview\<song_name> Teaser.mp3` whenever `format_mode == "full_arrangement"` and a hook video is generated.

---

### 2026-08-10: Keysight Compression (libx265 vs NVENC) & Re-export Skip Bug
- **Issue**: Keysight export videos were skipped by `handbrake_bot.py` even when uncompressed, or only compressed by ~50% when using GPU NVENC.
- **Lesson**: 
  1. `handbrake_bot.py` must verify the actual video stream codec (`hevc` / `h265`) via `ffprobe` rather than assuming existence of a `_RAW` file means the output is compressed.
  2. For Keysight piano waterfall recordings with large black backgrounds and moving notes, CPU `libx265 -preset veryfast -crf 22` achieves **90–92% file size reduction** (e.g. 907 MB -> 82 MB) due to deep macroblock tree-partitioning and spatial RDO, whereas hardware NVENC (`hevc_nvenc`) with fixed block sizes only achieves ~50–60%.
- **Resolution**: Updated `handbrake_bot.py` with `is_already_compressed()` using `ffprobe` codec detection, and configured CPU `libx265 -preset veryfast -crf 22` utilizing all 16 Ryzen 7800X3D threads.

---

### 2026-06-11: New PC Migration & Steam Paths
- **Issue**: Transitioning to a new PC left several hardcoded `D:\Antigravity Music` paths broken.
- **Lesson**: Steam on the new PC is on the `C:` drive. Keysight is at `C:\Program Files (x86)\Steam\steamapps\common\Keysight`.
- **Resolution**: Implemented dynamic configuration loading across all sub-scripts leveraging `settings.py` so paths can be changed on-the-fly, falling back to verified `C:\` locations.

---

### 2026-06-11: Redundant FastAPI Routes
- **Issue**: Two `/api/settings` routes existed in `main.py`. FastAPI resolves routes sequentially — the top route shadowed the lower one, wiping directory paths on save.
- **Resolution**: Removed the redundant Pydantic model and old handlers. The `settings.py` integration now serves `/api/settings` exclusively.

---

### 2026-06-11: MuseScore Template & Score Path Automation
- **Issue**: MuseScore template files prone to accidental overwriting. Score files scattered under global documents.
- **Resolution**: `C:\Dev\meloscribe-app\Scores` is now the workspace scores directory. `r2_uploader.py` (formerly `kofi_zipper.py`) auto-copies the template to `{song}.mscz` and launches two instances (MIDI reference + score copy) with a `time.sleep(1.5)` delay between spawns to avoid race conditions.

---

### 2026-06-18: MuseScore Startup Race Conditions & SSH Key Validation
- **Issue**: Dual MuseScore launch causes race conditions (one window opens blank). `check_sniper.bat` blocks silently if SSH key is missing.
- **Resolution**: Added `time.sleep(1.5)` delay between spawns. Added key existence validation and `-o StrictHostKeyChecking=accept-new -o ConnectTimeout=5` to SSH command. Fixed NTFS permissions on SSH key file via PowerShell (user-only access). Changed `echo.` to `echo` for Linux compatibility.

---

### 2026-06-18: Missing API Tokens & Hidden Directory Backups
- **Issue**: Social media tokens missing after PC migration. Backup drive `X:\` did not contain `.gemini` hidden directories.
- **Lesson**: Standard backup tools omit hidden dot-directories. Always explicitly include `.gemini`, `.ssh`, `.config` in backup configurations.
- **Resolution**: Re-authenticated all platforms via the Settings tab. All tokens now also stored in `C:\Dev\meloscribe_credentials_backup.json`.

---

### 2026-06-18: ngrok Tunnel offline (ERR_NGROK_3200) & stdout encoding crash
- **Issue**: ngrok failed to connect to Uvicorn. Additionally, `UnicodeEncodeError` on `→` character when printing to Windows console (CP1252).
- **Lesson**: ngrok resolves `localhost:8787` to IPv6 `::1` on Windows. Uvicorn runs on IPv4. Must explicitly target `127.0.0.1:8787`.
- **Resolution**: Extracted `ngrok.exe` to `tools/ngrok/ngrok.exe`. Updated startup command to `127.0.0.1:8787`. Replaced `→` with `->` in print statements.

---

### 2026-06-18: PowerShell Out-File creates UTF-8-with-BOM — breaks Python JSON
- **Issue**: `Out-File -Encoding utf8` in PowerShell 5.x prepends a BOM. Python's `json.load()` throws `JSONDecodeError` on BOM-prefixed files.
- **Resolution**: Always write JSON files using the file write tool (no BOM). Alternatively use `Out-File -Encoding utf8NoBOM` (PS 6+) or `[System.IO.File]::WriteAllText()`.

---

### 2026-06-18: Ko-Fi cookie names are custom, not standard ASP.NET names
- **Issue**: Assumed cookie name `.AspNetCore.Identity.Application`. Actual names are `kofi_identity_cookie` and `kofiweb.session`.
- **Lesson**: Never guess cookie names. Always verify via browser DevTools (F12 → Application → Cookies → domain).
- **Resolution**: Updated `kofi_cookie.txt` with correct names. CSV sync then immediately imported 16 historical sales.

---

### 2026-06-21: Electron Binary Extraction & VBS Script Launcher Issues
- **Issue**: Node.js v26.3.0 fails to extract Electron binaries silently. VBS shortcut launcher blocked by Windows Script Host policy.
- **Resolution**: Manually extracted `electron-v35.7.5-win32-x64.zip` from cache via `Expand-Archive`. Replaced VBS shortcut generator with `create_shortcut.ps1` (Shell COM object). Shortcut now points directly to `start_meloscribe.bat` with `WindowStyle = 7`.

---

### 2026-06-21: Dynamic OCI Queue Filename Resolution & Combined SSH Invocation
- **Issue**: Separate SSH queries per queue item caused high latency.
- **Resolution**: Combined Python SQL dump + `find` command in a single SSH call. Relative paths under `staging/` matched by prefix/suffix pattern (`slow` vs. normal). DELETE/UPDATE exposed as direct SQL SSH wrappers in the React UI.

---

### 2026-06-21: Systemd Journalctl Hangs & Log Redirection
- **Issue**: `systemctl status` and `journalctl` hung on OCI server due to large journal logs. Triggered API timeouts.
- **Lesson**: Use `systemctl is-active` for lightweight checks. Route logs to a plain file via `StandardOutput=append:/path/to/log` and read with `tail`.
- **Resolution**: Replaced all status calls with `systemctl is-active`. Service now logs to `/home/ubuntu/meloscribe/uploader.log`.

---

### 2026-06-22: One-click OAuth Flows for Instagram/Threads
- **Issue**: Token generation from Facebook Graph API Explorer is tedious. Tokens expire.
- **Resolution**: Created `ig_auth.py` and `threads_auth.py`. Unified `localhost:8080` callback server captures codes dynamically via `state=fb`/`state=threads` routing. Integrated into `main.py` with UI buttons and manual fallback.

---

### 2026-06-22: Browser Window fails to open from background subprocess
- **Issue**: `webbrowser.open()` fails from Electron-spawned subprocesses/service threads.
- **Lesson**: Use `os.startfile(url)` for Windows — leverages native Explorer shell, opens in running browser instance.
- **Resolution**: Updated all `*_auth.py` files to prioritize `os.startfile()`. Subprocess and `webbrowser` kept as fallbacks.

---

### 2026-06-22: FastAPI POST Requests without Request Body raise 422
- **Issue**: `POST /api/threads/authorize` with no payload throws `422 Unprocessable Content` before handler runs.
- **Lesson**: Declare body models as `Optional[Model] = None`. Client must send `{}` with `Content-Type: application/json`.
- **Resolution**: Updated FastAPI signatures with optional defaults. Frontend `handleOAuth` now sends empty JSON objects.

---

### 2026-06-22: Meta OAuth Domains and Redirect URIs Configuration
- **Issue**: Facebook/Instagram throws "URL not in app's domains" during OAuth.
- **Lesson**: Meta requires the exact redirect domain (e.g. ngrok subdomain) in App Settings > Basic > App Domains AND Facebook Login > Settings > Valid OAuth Redirect URIs.
- **Resolution**: Whitelist active ngrok domain in Meta Developer Dashboard, or use manual token input fallback.

---

### 2026-06-22: Threads API Requires Dedicated Threads App ID
- **Issue**: Error `4476002: In der Anfrage wurde keine App-ID übermittelt.`
- **Lesson**: Threads API requires a **separate** Threads App ID/Secret found at the bottom of App Settings > Basic — distinct from the primary Meta App ID at the top.
- **Resolution**: `threads_auth.py` and `ig_auth.py` now load `threads_app_id`/`threads_app_secret` from `settings.json`.

---

### 2026-06-25: Oracle VM missing packages & Git credential blocks
- **Issue**: Oracle VM lacked `git`, `nginx`, `certbot`, `sqlite3`. HTTPS git operations block indefinitely without cached credentials.
- **Lesson**: Never assume server tools are pre-installed. For automated deploys, use SCP or SSH deploy keys to bypass interactive credential prompts.
- **Resolution**: Installed all packages. Backend deployed via SCP. Server now pulls from public `https://github.com/meloscribe/meloscribe-backend.git`.

---

### 2026-06-25: PowerShell single-quote quoting hell in SSH commands
- **Issue**: Passing Python one-liners containing single quotes via PowerShell SSH commands causes parser errors due to nested quoting conflicts.
- **Lesson**: Never inline complex Python in a PowerShell SSH string. Write a `.py` file locally, SCP it to `/tmp/`, run it remotely, then delete it.
- **Resolution**: Standardized pattern: write script locally → `scp` to `/tmp/` → `ssh python3 /tmp/script.py` → `rm /tmp/script.py`.

---

### 2026-06-26: Ko-Fi upload replaced by Cloudflare R2 (`r2_uploader.py`)
- **What changed**: Ko-Fi was the old delivery platform for sheet music packages. Replaced by a direct Cloudflare R2 upload flow (Paddle handles payment; backend generates presigned download links).
- **New script**: `tools/r2_uploader.py` — 3 modes: `--zip_only` (build ZIP, wait for MuseScore PDF), `--upload_only` (upload existing ZIP), no flags = full flow.
- **Legacy**: `kofi_zipper.py`, `kofi_csv_sync.py`, `kofi_cookie.txt` moved to `tools/legacy_kofi/` — not deleted.
- **settings.json**: `doKofi: false`, `doR2: true` — workflow toggle updated accordingly.
- **boto3**: Installed in local `.venv` for S3-compatible R2 uploads.
- **Lesson**: Keep old platform scripts in a `legacy_` folder before deleting. R2 bucket is `meloscribe-sheets`. Credentials stay in `settings.json`, never in the script.

---

### 2026-06-26: FFmpeg Binärdateien blockieren GitHub Push (>50 MB Warnung)
- **Issue**: `git push` schlug fehl — GitHub warnte vor Dateien über 50 MB in `tools/ffmpeg/bin/` (z.B. `ffmpeg.exe`, `ffprobe.exe`). Die Binaries waren versehentlich committed worden.
- **Lesson**: Große Binärdateien (Executables, Renderer-Outputs) niemals committen. `.gitignore` schützt nur ungetrackte Dateien — bereits getrackte Dateien müssen explizit aus dem Index entfernt werden.
- **Resolution**: `git rm --cached tools/ffmpeg/ -r` entfernt die Dateien aus dem Git-Index ohne sie lokal zu löschen. Anschließend `tools/ffmpeg/` in `.gitignore` eingetragen. Lokale Binaries bleiben vollständig erhalten.

---

### 2026-06-26: GitHub Desktop — Drei Repos, ein Account (Collaborator-Trick)
- **Issue**: Die drei Repos (`meloscribe-app`, `meloscribe-backend`, `meloscribe-frontend`) gehören der GitHub-Organisation `meloscribe`, aber GitHub Desktop ist unter dem persönlichen Account `ventoba` angemeldet.
- **Lesson**: GitHub Desktop unterstützt mehrere lokale Repos reibungslos — unabhängig davon, welchem Account oder welcher Organisation sie gehören. Der entscheidende Schritt ist, dass `ventoba` als **Collaborator** in den Org-Repos eingetragen ist. Danach können alle drei Repos normal über GitHub Desktop gepusht und gepullt werden, ohne den Account zu wechseln.

---

### 2026-07-02: Payment Gateway Migration from Paddle to Stripe Checkout
- **What changed**: Migrated the entire payment and checkout system from Paddle to Stripe. Removed client-side SDK integration and inline checkouts, moving to a redirect flow using FastAPI generated Stripe Checkout sessions.
- **Key implementation details**:
  - Frontend: Replaced the inline Paddle iframe with a secure Stripe pay button in `PaddleModal.tsx` triggering `/api/checkout/create-session` and redirecting client side.
  - Backend: Added `stripe` library. Configured `/api/checkout/create-session` for session generation using ad-hoc price data. Configured `/api/webhooks/stripe` to handle checkout completion (active) and refund callbacks.
  - Security: Configured git assume-unchanged index flags on local credentials configurations (`settings.json`, token files) to prevent private credentials leakage to public GitHub repositories.
  - Compatibility: Status mapping in admin views handles `🟢 Active`, `🔴 Refunded`, and `🔴 Deactivated` safely using includes checks.

---

### 2026-07-02: Smart Batch Ingest Queue, Deep Delete, and Clear Toggle Naming
- **Smart Batch Queue Workflow**:
  - Mass processing (~30 songs) is most efficient when separated: (1) Staging creates Cakewalk directories and saves MIDIs in one batch; (2) User does audio export at their own pace; (3) Background processor loop processes only songs with `.wav` exports ready.
  - Using a SQLite-backed queue with status flags (`initialized`, `processing`, `active`, `failed`) prevents double processing of active/failed items on repeated loops.
- **Deep Cleanups (Residual Files Prevention)**:
  - Deleting arrangements from the website catalog (`songs.json`) leaves file residues on local disk and cloud storage.
  - Created a backend-driven deep cleanup route `DELETE /api/website/songs/{id}?delete_assets=true`. It recursively removes:
    - Cakewalk local directories: `{cakewalk_dir}/{song}` & `{cakewalk_dir}/{song} Easy`.
    - Local package ZIPs: `{packages_dir}/{song} Full Package.zip`.
    - Cloudflare R2 bucket objects: `{song} Full Package.zip` and all files under prefix `{song}/`.
- **Descriptive UI Naming**:
  - Parameter-based nomenclature (e.g., `'portrait addon'`) is unintuitive. Replaced it with action-oriented naming (`'fill bottom background'`) in both Master and Manual configuration tabs to clearly explain visual overlays.

---

### 2026-07-03: Queue Casing Alignment, Song Purging, and Preview Labels
- **Linux Server Case-Sensitivity**:
  - Windows filesystems are case-insensitive, but S3-compatible Cloudflare R2 buckets and Linux servers are strictly case-sensitive. If files are uploaded with a casing mismatch compared to the catalog `songs.json`, it leads to 404 errors during client-side downloads or audio stream initialization.
  - Aligned database `batch_ingest_queue` song names and local folders to match the exact casing of their primary MIDI files and WAV exports (e.g. `River Flows in You`, `We Wish You a Merry Xmas`).
- **Batch Queue Cleanup & Old Song Purging**:
  - To prevent duplicates on the website, deprecated/renamed catalog items such as `My singing Monsters Plant Island` and `Golden Brown x Love Story` were deleted from `songs.json`. Unprocessed folders in `C:\Cakewalk Projects` with missing WAV exports (`Experience`, `Golden Brown`, `Nuvole Bianche`) were permanently deleted.
- **Clarity in Video Previews**:
  - Adjusted the frontend website preview modal (`PaddleModal.tsx`) to show explicit overlay messages specifying that full arrangement previews are only 60-second excerpt clips, preventing client confusion (skipped for viral parts, which contain the full short video).
- **Aborting Ingest Loops on Error**:
  - Stur continuing the batch processor when a critical tool (like Keysight) fails leads to cascaded failures across the entire queue. The worker now catches errors and immediately breaks the loop (`should_abort_queue = True`), preventing mass-fail statuses.
- **Robustness in GUI App Spawning**:
  - `os.startfile` is highly dependent on shell associations. When running scripts inside headless subprocesses, this can fail. Implemented a fallback to `subprocess.Popen` in `keysight_bot.py` to ensure Keysight launches reliably under all conditions.
- **Logging Visibility**:
  - Stdout prints inside thread workers are invisible to web UI debug consoles. Migrated all batch worker logging to the backend's in-memory `log_error` buffer to expose real-time debugging information to the client.
- **Auto-Reset Stuck Queue Items**:
  - If the server restarts or crashes while a song is processing, its DB status remains stuck at `'processing'`. Upon next boot, the frontend shows "running" indefinitely. Added an automatic database update statement at `startup_event` to revert any stuck `'processing'` tasks to `'initialized'`.
- **Zombie Python Backend Processes**:
  - When restarting the Electron app, old FastAPI python processes may remain orphaned in the background, listening on port 8787. Electron fails to bind to the port but successfully connects to the old, glitched backend instance. Explicitly terminating all python processes (`Stop-Process -Name python -Force`) resolves this layout lock.
- **PyAutoGUI FailSafe Exception**:
  - In multi-monitor setups or when users slightly nudge their mouse during automated rendering loops, PyAutoGUI's default fail-safe mechanism gets triggered (detecting coordinates as screen corners like 0,0 or virtual offsets). Disabled this safety feature (`pyautogui.FAILSAFE = False`) in `keysight_bot.py` since the rendering loop is a background process.
- **Multi-Monitor PIL ImageGrab Crash**:
  - Pillow's `ImageGrab.grab(all_screens=True)` API crashes on Windows with `OSError: screen grab failed` when multiple displays have mismatched resolutions or virtual offsets. Monkey-patched `PIL.ImageGrab.grab` in `keysight_bot.py` to force `all_screens=False`, limiting the grab to the primary monitor (where Keysight resides) and eliminating the crash.
- **Pythonw.exe Window Station Limitations**:
  - Running a python backend using `pythonw.exe` inside electron (hidden GUI subsystem mode) spawns the thread in a non-interactive Windows station. This prevents libraries like PyAutoGUI and Pillow from taking screen grabs or simulating keystrokes under certain security templates, yielding GDI errors. Swapping to `python.exe` runs inside the normal console session and restores screen capture permissions.
- **Electron spawn windowsHide constraints**:
  - Spawning subprocesses in Node/Electron with `windowsHide: true` hides the spawned window but can strips GDI screenshot access for screen grab operations on Windows 10/11. Setting `windowsHide: false` forces execution inside an active window station, restoring screen capture permissions.

---

### 2026-07-03: Cover Crop, Text Center, and Theme Database Resolution
- **Cover Image Aspect Ratio & Keyboard Crop**:
  - The Keysight background templates contain a keyboard at the bottom. The card wrapper has an aspect ratio of `[4/5]` (0.8). Originally, the image used a height of `h-[125%]`, which scaled the keyboard into the visible region. Increasing the image height to `h-[140%]` with `object-cover object-top` successfully crops off the bottom 28.5% of the image, hiding the keyboard keys completely.
- **Vertical Text Alignment over Covers**:
  - Originally, the clean cover text overlay used `inset-x-0 bottom-0 pb-14 justify-end`, which pushed title and author text too close to the bottom. Shifting the container to `inset-0 justify-center` centers the text overlay vertically and horizontally in the visible cover area.
- **Theme Mapping and Background Glow Alignment**:
  - Storing the explicit `theme` property (`warm`, `cold`, `green`) inside `songs.json` ensures the clean cover script `generate_and_upload_clean_covers.py` regenerates correct textless backgrounds instead of cycling randomly. We implemented automated database lookup for `theme` in `upload_bot.py` via `analytics.db` queue querying, and aligned the Tailwind background glow borders/gradients (`neon-pink` for warm, `neon-cyan` for cold, `emerald`/`teal` for green) with the song's actual color theme.
- **Dynamic Theme-Specific Hover Glow Styles**:
  - Relying on difficulty to switch card hover styles (e.g., `sheet-card-alt` for Original vs default for Easy) did not match the song's background color theme. Defining explicit class name variations (`sheet-card-warm`, `sheet-card-cold`, `sheet-card-green`) and mapping them to their corresponding hover states in `index.css` enables exact, visual theme alignment on hover.
- **Cover Text Readability & Radial Vignette**:
  - Small text sizes (`text-sm` to `text-lg`) on cropped covers can look lost. Increasing font sizes (`text-base` to `text-2xl` for titles, `text-[10px]` to `text-sm` for artists) fills the visual space better. Adding a radial dark gradient overlay (`bg-[radial-gradient(circle_at_center,rgba(0,0,0,0.45)_0%,transparent_75%)]`) behind the text overlay maximizes contrast against bright backgrounds without muddying the card design.
- **Unified Audio/Video Slice & Bandwidth Optimization**:
  - Slicing the audio hover MP3 file to match the exact start/end times of the video hook editor aligns the hover sound with the video preview. It also cuts audio file size (from ~4MB to under 1MB), significantly reducing user bandwidth and removing hover latency on the live website.
- **Orange Theme Alignment for Warm Designs**:
  - Aligning the warm theme color to orange-500 (glow border and hover shadows) matches the shopping bag/download button icons. Dynamically mapping button icons (`Download` & `ShoppingBag`) using theme-specific classes (`text-neon-cyan` for cold, `text-emerald-500` for green, `text-orange-500` for warm) creates a visual harmony across card components.
- **Global gitignore Bypass via Whitelisting**:
  - When `.mp3` is globally ignored in a parent directory's `.gitignore` (`*.mp3`), local additions to `public/audio-previews/` are silently ignored during git commits, preventing deployment of hover previews. Adding an explicit whitelist exception (`!website/public/audio-previews/*.mp3`) to the `.gitignore` restores proper file tracking and ensures successful asset publication.
- **Interpreting Real-Time Process Progress**:
  - Replacing simple blocking `subprocess.run` with line-by-line `subprocess.Popen` reading enables real-time terminal output processing (e.g. tracking `PROGRESS:XX%` outputted by `video_generator.py`). Storing this progress percentage inside a dedicated SQLite column updates the polling frontend dashboard instantly, providing visual progress feedback during long-running tasks.
- **Key-Based React List Reconciliation**:
  - Using unstable list indices (`key={idx}`) as unique element identifiers in React mapping causes complete DOM tree reconstructions on array state updates (causing flashes or visual reloading). Replacing them with stable, unique properties (`key={songName}`, `key={email}`) enables exact reconciliation, so React only updates the modified attributes (e.g. status badge or progress percent text node) without touching the rest of the element.
- **Unified Local-Server Backend Sync**:
  - When a project is split into a local application client repository (`meloscribe-app`) and a production server control plane API repository (`meloscribe-backend`), backend endpoints and JSON metadata structure modifications (e.g. `/api/public/audio-stream` and `stripePriceId` mappings) must be copied, committed, and pulled on the production VM to maintain feature alignment.

---

### 2026-07-04: Automated Packages, Double-Render Check, Audio Hover Race Condition, and Catalog Sync
- **Automated Copying to Packages**:
  - The new R2-based pipeline uploads files individually and bypasses the legacy zip-bundler, leaving the `packages/` folder empty. Integrating a file copier routine into the R2 upload pipeline in `upload_bot.py` copies all processed customer assets (PDF, normal/slow MIDI, and normal/slow MP4) to `C:\Dev\meloscribe\packages\{song_name}\` automatically.
- **Double-Render Check (FFmpeg & R2 Speedups)**:
  - Repeatedly running the batch queue on existing items triggers slow video renders and uploads. Adding exit-early checks in `video_generator.py` (checking if output file exists) and `upload_bot.py` (checking if R2 size matches local size) enables instant queue runs.
- **Audio Hover Autoplay Race Condition**:
  - If a user moves their mouse over a card and quickly leaves before the audio is loaded, a pending `canplay` event listener eventually fires and starts playing the audio, leaving it playing indefinitely. Using a `hoveredSongIdRef` to store the active hovered card ID and verifying that `hoveredSongIdRef.current === song.id` inside `doPlay()` before launching audio playback prevents this race condition completely.
- **Unified Catalog Sync & Deduplication**:
  - Adding songs to the website from different points causes duplicate entries and mismatched prices/themes in `songs.json` files. Creating a dedicated python synchronization script that matches entries case-insensitively, updates prices/themes to match the batch database canonical values, and filters out dummy/duplicate IDs ensures a 100% synchronized and correct production catalog.
- **Widescreen Video Metronome Injection without Re-encoding**:
  - Re-encoding long slow videos to mix audio tracks is extremely slow and degrades video quality. Using `-c:v copy` in FFmpeg while mixing the synthesized MIDI metronome wav with the video's audio tracks allows lossless, instant click-track generation and integration, taking less than 2 seconds per video.
- **Force-Regenerating Cached Assets**:
  - Scripts that check for file existence (such as `cover_generator.py`) will skip execution for previously generated assets, keeping outdated versions even if metadata/themes change in the DB. Force-deleting target assets before running the generation routine resolves this caching issue.

---
### 2026-07-06: Stripe SDK StripeObject dict-like lookups, ntfy.sh Migration, and HTML5 Audio Seek Stability
- **Stripe SDK StripeObject dict-like lookups**:
  - In newer versions of the Stripe Python SDK (v8.x+), returned API resources and objects (subclasses of `StripeObject`) no longer inherit from `dict`.
  - While they implement custom `__getitem__` (allowing bracket lookups like `obj["key"]`), they do not define a `.get()` method.
  - Calling `obj.get("key")` triggers `__getattr__("get")`, which internally attempts to resolve the dictionary key `"get"`. If it's missing, it throws a `KeyError: 'get'`, which bubbles up as a generic error message `get` and results in HTTP 500 crashes.
  - Converting the `StripeObject` to a native dictionary via `.to_dict()` recursively deserializes all nested structures into dictionaries, allowing all standard dictionary lookup methods (like `.get()`) to work safely without modifying the rest of the application code.
- **Replacing Pushbullet with ntfy.sh**:
  - Pushbullet API is no longer supported. We migrated to `ntfy.sh` (Notify) — a free, registration-less pub-sub service.
  - Replaced `pushbullet_token` settings key with `ntfy_topic` globally in FastAPI settings templates, `settings.json`, and frontend UI (`SettingsTab.jsx`).
  - Implemented `send_ntfy_notification` in `upload_bot.py` via a simple HTTP POST request to `https://ntfy.sh/{topic}` sending the full description string as raw request bytes, along with custom headers for notification Title and icon tags.
- **HTML5 Audio Seek Stability**:
  - In React, assigning `currentTime` to a newly constructed `Audio` instance while its `readyState` is `0` (before metadata is loaded) causes browsers to drop the seek request or start playing from arbitrary offsets.
  - Delaying the `currentTime` assignment until the audio resource is ready (`readyState >= 3` or inside the `canplay` callback) guarantees that the seek offset is applied reliably.

---
### 2026-07-07: Tab State Preservation via CSS Display, Settings Self-Healing Parser, and Purchase Email Delivery Fix
- **Tab State Preservation via CSS Display**:
  - Replacing conditional component rendering (`{activeTab === 'name' && <Component />}`) with visibility toggles (`style={{ display: activeTab === 'name' ? 'block' : 'none' }}`) inside the React DOM tree avoids complete unmounting and remounting when navigating between tabs. This preserves all tab states, cursor focus, scroll positions, and local component states.
- **Background Auto-Refresh polling**:
  - Incorporating data synchronizing functions (`fetchSongs()`) inside background setInterval callbacks of mounted tab components enables silent background updates of catalog tables without introducing flashing loading animations or disrupting focus.
- **Config Mismatch on musescore_dir**:
  - Hardcoded paths that resolve dynamically relative to the application workspace (such as `musescore_dir` default resolving to `C:\Dev\meloscribe-app\Scores`) will result in missing files if the user stores them in a separate directory (`C:\Dev\meloscribe\Scores`). Keeping paths hardcoded to standard absolute folders (`C:\Dev\meloscribe\Scores`) keeps configuration uniform.
- **Self-Healing Settings Credentials Parser**:
  - If `settings.json` is reset or recreated, it loses the Cloudflare R2 credential keys, causing R2-dependent API routes and uploader scripts to crash. Adding a parser fallback inside `load_settings()` that checks for missing credentials, reads them from the `.env` file in the workspace root, and saves them back to `settings.json` automatically repairs the configuration on the fly.
- **HTML5 Audio Engine Exhaustion & Stale canplay Listeners**:
  - Lazy-instantiating multiple `new Audio()` elements on hover and calling `.addEventListener('canplay', ...)` creates a massive memory and listener leak when users hover/leave rapidly. When `canplay` finally fires on discarded audio elements, overlapping audio files start playing in the background with no way to stop them. Eventually, the browser exhausts its concurrent media stream limit (throwing `AccessDenied` or `NotAllowedError`), crashing all audio hover features on the site.
  - Overhauling the code to use a **single, global, mutable `Audio` element** via `useRef` and assigning callbacks directly using `audio.oncanplay = ...` (which automatically overwrites any previous listener) completely eliminates event listener leaks, resource exhaustion, and audio overlaps.
- **Window Blur & Page Scroll Audio Cancellation**:
  - React's `onMouseLeave` DOM listener can fail to fire when users scroll the page (moving the card out from under the cursor without moving the mouse) or when they switch windows/tabs (losing window focus). Subscribing to global `window` `blur` and `scroll` event listeners to programmatically call the hover stop logic guarantees that previews immediately cease playing as soon as they Alt-Tab, click away, or scroll.
- **Database Lock Remediation via WAL Mode**:
  - Concurrent operations from FastAPI threads and background uploader/sniper bots were causing SQLite write transactions to block read access, triggering `database is locked` HTTP 500 errors. Enabling SQLite Write-Ahead Logging (`PRAGMA journal_mode=WAL;`) on the database file allows concurrent read operations during active write transactions, fully resolving lock contention.
- **Dynamic API Key Key Cache Bypass**:
  - The local API proxy cached `server_api_key` in a global variable. When the key was dynamically generated or synchronized on disk, the running FastAPI process continued using the outdated cached value (or `None`), leading to authorization failures (Cloudflare HTML 403 pages) and `JSONDecodeError` proxy crashes. Removing the cache variable and reading directly from `settings.json` on each call ensures key changes take effect immediately without requiring process restarts.
- **First-Load Tab Flickering & Silent Background Polling**:
  - The desktop app previously activated loading screens on every polling tick whenever lists were empty, causing sections to disappear and show "Loading..." repeatedly. Introducing `hasLoaded` state triggers and `fetchingRef` transaction locks ensures loading overlays are only rendered during initial component mount. All subsequent background auto-refreshes update data silently in place.
- **Stripe Webhook Purchase Email Delivery Fix**:
  - Stripe checkouts failed to trigger purchase confirmation emails to buyers because the required `resend_api_key` field was missing from local and remote `settings.json` configurations.
  - Enhanced the credentials fallback logic inside `settings.py`'s `load_settings()` to automatically parse the Resend API key and social API keys from local backup `C:/Dev/credentials.json` and save them into `settings.json` when missing.
  - Synced the updated files to the VM and restarted the uvicorn systemd service, bringing the Resend automated post-purchase email delivery flow back online.

---

### 2026-07-07: Website Backend Admin Route Recovery, Gemini Key Fallback, and Seamless Tab Reloading
- **Gemini Key Fallback Ingestion**:
  - Added the `gemini_api_key` recovery check inside the self-healing credentials fallback in `settings.py`. This ensures the Gemini AI key is dynamically restored from `C:/Dev/credentials.json` and written to local `settings.json` if it gets missing.
- **Website Backend Admin Route Recovery (404 fixes)**:
  - An earlier commit (`821810d`) incorrectly wiped several administrative endpoints from the FastAPI `main.py` source. This caused direct `404 Not Found` API responses when the app attempted to access `/api/admin/orders` or `/api/admin/packages` on the VM server.
  - Recovered the deleted administrative routes (including package lists, file upload/delete, customer orders query, reset downloads count, and status toggles) along with the `verify_admin` authentication helper. Deployed the restored codebase to the VM server and restarted the backend service, instantly restoring these features.
- **Seamless Reloading for Purchases and R2 Tabs**:
  - The react components for Purchases and R2 song packages previously showed full-screen loading placeholders (`Loading order log...` and `Querying Cloudflare R2...`) on every background polling interval. This led to persistent UI flickers every 10 seconds.
  - Resolved this by updating the loader conditions to only render when the list states are empty (`loading && list.length === 0`). Background updates now happen silently, preserving active scroll states and table selections.
- **Renaming Visualizer Video to Video**:
  - Changed the download buttons for free products in `PaddleModal.tsx` on the website. Replaced "Visualizer Video" with "Video" to maintain terminology consistency across the landing page and post-purchase page.
- **Local Video Watermarking Upload Pipeline & Instant Downloads**:
  - Transcoding video files on the server on-the-fly or in background tasks triggers major latency and Nginx Gateway Time-outs for customers during downloads.
  - Resolved this by reverting the server-side video transcoding to fast presigned R2 redirects (0.1s execution time). 
  - Integrated the video watermarking step (`"meloscribe.dev"`) directly into the local `upload_bot.py` script. When new music assets are published, the script uses local FFmpeg to automatically watermark the temporary copy of the video before uploading it to Cloudflare R2, leaving original local assets intact while guaranteeing latency-free watermarked video downloads for customers.
  - Created a local batch script `watermark_and_upload_all_local.py` that loops through all existing song folders, watermarks their videos locally using local CPU/GPU speed, and pushes the watermarked versions to R2 in bulk, avoiding VM performance limitations.
  - **Elegant Woodblock Metronome Synthesis**: Redesigned the metronome synthesis logic across all tools (`video_generator.py`, `video_shop_editor.py`, and `add_metronome_to_all_slow_videos.py`) to generate an elegant, warm woodblock/side-stick click (harmonics at 1100 Hz and 2200 Hz with fast exponential decays) instead of synthetic high-pitched beeps. Removed downbeat emphasis so that all beats sound identical. Created `remix_single_song_metronome.py` to easily mix and update R2 assets for single songs.
- **Rebranded Email Templates**:
  - Updated three transaction/marketing email templates (`send_purchase_delivery_email`, `send_opt_in_email`, and `send_broadcast_email`) inside `main.py` to keep branding consistent. Replaced `"piano & sheet music"` with the brand slogan `"Arranged by ear. Played by you."` and aligned the title block styling to a cyan logo brand name with a magenta underline.

---

### 2026-07-08: Pinterest Description Template, TikTok client_key Alignment, and Playwright Demographic Sync Clicks
- **Pinterest Settings Integration**:
  - Discovered that the Pinterest description template configuration option was in the settings data model (`desc_template_pinterest`) but lacked an input field in the Settings tab UI. Added a dedicated textarea input field under the Pinterest Integration section inside [SettingsTab.jsx](file:///c:/Dev/meloscribe-app/tools/meloscribe/frontend/src/components/SettingsTab.jsx) to make it fully customizable. Also updated [stage_to_server.py](file:///c:/Dev/meloscribe-app/tools/stage_to_server.py) to sync `pinterest_tokens.json` to the VM uploader server.
- **TikTok client_key developer mismatch**:
  - The local desktop app's OAuth authorization failed with a `"client_key"` error when the user attempted to link TikTok. Found that the self-healing credentials parser inside `settings.py` was skipped because `settings.json` and backend fallback constants had a placeholder default key `awe54p8mg3xasm1l` pre-configured. Resolved this by updating all backend fallback client key constants and default settings inside `settings.json`, [settings.py](file:///c:/Dev/meloscribe-app/tools/meloscribe/backend/settings.py), [tiktok_auth.py](file:///c:/Dev/meloscribe-app/tools/meloscribe/backend/tiktok_auth.py), and [tiktok_setup.py](file:///c:/Dev/meloscribe-app/tools/tiktok_setup.py) to use the user's correct sandbox developer client key `sbawllqdpf3yk6g8kh` from `credentials.json`.
- **Playwright Demographics Scraper popover/dropdown downloads**:
  - The local demographics scraper ([scrape_demographics.py](file:///c:/Dev/meloscribe-app/tools/scrape_demographics.py)) previously failed on Facebook Business Suite and TikTok Studio because it only clicked the initial "Export" or "Download" buttons. On both platforms, the initial click only opens a dropdown or modal menu which requires format selection before the file is generated.
  - Rewrote the scraper steps: on Facebook, it now clicks the primary "Exportieren" button, waits for the dropdown, and then clicks the visible `"Als CSV exportieren"` option within the download-monitoring block. On TikTok, it clicks "Download" to open the format dialog, clicks the `"CSV"` radio label, and then clicks the final `"Herunterladen"` button inside the modal to trigger the download.

---

### 2026-07-08: Pre-stripping Board Validation, Sandbox Telemetry Cleanup, Calendar-Based Growth, and website popup parameters
- **Board Validation Sequence**:
  - String formatting helpers that strip suffix indicators (such as `" Easy"`) must run *after* checking the version for uploader routing. Stripping the song name first erases the version indicator, causing all pins to resolve to the Intermediate board. Explicitly caching `is_easy` before string manipulation fixes this.
- **Purging Sandbox Database Telemetry**:
  - Standard sandbox Stripe/Paddle payment checkouts generate transaction records under test emails (such as `2beers.sm@gmail.com`). These entries register as actual sales, corrupting telemetry charts like "Top Selling Sheets" with fake mock entries. Running a target delete query against `revenue` and `purchases` tables for these specific mock emails clean up analytics charts instantly.
- **Calendar-Based 7-Day Growth & First-Snapshot Baselines**:
  - Using index offsets to select baseline dates (such as `len(growthData) - 7`) assumes a continuous daily sync cycle. For sparse sync periods, index subtraction selects incorrect history offsets. Transitioning to datetime objects to find the database date closest to 7 days before the latest sync resolves this. Additionally, querying the earliest recorded views of a song as a baseline instead of `0` avoids massive false view growth metrics for newly synced arrangements.
- **URL Parameter Modal Hooks & Address Bar Cleanup**:
  - Adding deep-linking URL query strings (`?song={slug}&version={version}`) to pin links directs traffic straight to specific product views. Implementing a `useEffect` hook on the catalog website to listen for these parameters, match song title slugs case-insensitively, and auto-open payment overlays makes landing page redirections seamless. Using `history.replaceState` to clear these parameters on modal close ensures the URL is cleaned up after interaction.

---

### 2026-07-09: Backend Refactoring (Monolith Splitting), Reference Safety & Git-Ignoring Shared Secrets
- **Monolith Splitting**:
  - Backend `main.py` grew to 6,500+ lines of code, making it difficult to maintain and prone to lock conflicts.
  - Splitting a monolithic server into FastAPI routers requires careful attention to shared in-memory variables (such as WebSocket connections and system logs deques). Primitive module imports behave like singletons but direct assignment breaks references in secondary routers. Using mutable containers (like lists, dictionaries, or custom classes) and modifying them in-place (e.g. `.clear()`) preserves the reference identity across all routers.
- **Ignore files & API key sync**:
  - Modularized `main.py` into five distinct files (`shared.py`, `routes_public.py`, `routes_admin.py`, `routes_settings.py`, `routes_workflow.py`), keeping only uvicorn initialization and base middlewares inside `main.py`.
  - Added `tools/meloscribe/backend/api_key.txt` to both workspace `.gitignore` files to prevent leakages while preserving automated SCP credentials synchronization. Added `api_key.txt` to `files_to_sync` in `routes_settings.py` so the keys automatically stay in sync between development and production.

---

### 2026-07-09: Purging ZIP Packaging, Order Deletion Endpoints, and Checkout UI Aesthetics
- **ZIP-Free Architecture**:
  - Abandoned ZIP archive packaging globally (both R2 and local packages).
  - Modified `upload_bot.py` to prevent ZIP compression, uploading only the loose assets (PDF, MIDI, slow MIDI, normal/slow MP4 videos) individually to Cloudflare R2, preventing customer download timeouts and unnecessary compression processing overhead.
  - Removed `{ id: 'zip', name: 'Full Package (.zip)' }` from the App's R2 view and removed the free ZIP download button from the website catalog's modal.
- **Admin Purchase & Revenue Deletion**:
  - Created a new secure endpoint `DELETE /api/admin/orders/{transaction_id}` in `routes_admin.py` (with a local Windows proxy).
  - Deleting an order from the app now deletes it from both the `purchases` table (making its public download link immediately invalid) and from the `revenue` database table (associated transaction log text matching), ensuring sandbox or test payments are excluded from the main analytics dashboards.
- **Checkout Button Aesthetic Sizing**:
  - Aligned the Stripe checkout button on the website to the identical padding (`py-3 px-4`), corners (`rounded-xl`), typography size (`text-sm`), and weight (`font-semibold`) of the free download buttons in `PaddleModal.tsx`, restoring UI harmony.

---

### 2026-07-10: Cloudflare Header Overrides, IP Currency Routing, and Dynamic UI Pricing Symbols
- **Cloudflare Header Overrides & Security**:
  - When hosted behind a secure proxy like Cloudflare, custom incoming headers such as `X-Forwarded-For` are overridden at the edge to prevent IP spoofing. Real client IPs are passed in `CF-Connecting-IP`, and their resolved countries in `CF-IPCountry`.
  - Checking `CF-IPCountry` first enables zero-latency currency routing, while an `ip-api.com` fallback (caching results to avoid rate limit exceptions) parses client IP loops reliably.
- **Dynamic Pricing Symbol Mapping**:
  - Moving the website catalog datasource from static file imports to a dynamic public API (`/api/public/songs`) allows the backend to rewrite pricing properties on the fly. The React frontend consumes the API list seamlessly, updating display badges automatically (e.g. `4 €` -> `4 $` or `4 £`).

---

### 2026-07-10: ProgressBar Parsing, Deep Delete R2 Credentials, Slug Collapsing, and Dynamic Social Labels
- **Progress Bar String Splits**:
  - Background shell subprocesses can print extra debug details on the same line as the progress indicator (e.g. `PROGRESS:50% (10s / 20s)`).
  - Attempting to parse this directly via `int(line.split(":")[1].replace("%", "").strip())` throws a `ValueError`.
  - Splitting by `(` first (`line.split(":")[1].replace("%", "").strip().split("(")[0].strip()`) discards the trailing details and leaves the raw integer.
- **Deep Clean R2 Credentials**:
  - If a function (like `run_deep_asset_cleanup`) looks for custom AWS S3 keys like `"r2_access_key_id"` and `"r2_secret_access_key"` instead of the standard `"r2_access_key"` and `"r2_secret_key"` keys used in `settings.json`, client initialization fails and cloud deletion is silently skipped. Always align uploader client key maps across all scripts.
- **Slug Hyphen-Collapsing**:
  - Python's uploader slug generation previously stripped non-alphanumeric chars (`re.sub(r'[^a-z0-9\s-]', '', slug)`), translating *"I Don't Know"* to `"i-dont-know"`.
  - The website React router replaces all non-alphanumeric sequences with a hyphen, yielding `"i-don-t-know"`.
  - Aligning the Python script to use `re.sub(r'[^a-z0-9]+', '-', slug).strip('-')` collapses all non-alphanumeric characters into single hyphens, eliminating URL discrepancies.
- **Dynamic Platform Description Labels**:
  - When uploading Easy, Tutorial, or Teaser arrangements, the uploader scripts must dynamically compile descriptive labels (e.g. `" Easy"`, `" Easy Tutorial"`, `" Tutorial"`, `" Teaser"`) instead of only checking for profile flags.
  - Stripping double tags (e.g. `"Easy"`) from the song title before generating video titles avoids doubled suffix strings on YouTube and Facebook titles.

---

### 2026-07-16: Cloud Server Catalog Synchronization, Teaser Outro, and Tutorial Practice Call-To-Action
- **OCI Server Dynamic Catalog Loads**:
  - The public `/api/public/songs` endpoint runs directly on the production OCI VM and queries the server's local `songs.json` file. Because FastAPI handles requests dynamically from disk on each invocation, modifying local files on the desktop developer PC does not update the cloud catalog.
  - Changes must be deployed via SCP to `/home/ubuntu/meloscribe/tools/meloscribe/backend/songs.json` or committed to Git to keep the public website synchronized. Since the server runs 24/7 independently of the local PC app, all catalog updates are persisted persistently.
- **Teaser Video Outros and Tutorial CTA Overlays**:
  - Outros for teaser/hook videos should display `"Full video coming soon"` as the primary line instead of `"Don't miss the tutorial"`.
  - For slow/tutorial videos, the morphing CTA overlay should change to `"save to practice later"` for a few seconds to encourage user engagement, while normal and easy normal videos retain their respective versions check hooks.

---

### 2026-08-09: Hook Editor Standby Crash & Crop Preview Video Remounting
- **Missing Lucide Icon Import Crash**:
  - `CustomThemeVideoPlayer.jsx` rendered a fallback standby card when a video failed to stream (`hasError === true`). Inside the reload button, `<RefreshCw />` was invoked without being included in the `import { ... } from 'lucide-react'` declaration at the top of the file.
  - When switching to the "hook editor" tab prior to the 9:16 portrait video being rendered on disk, the component errored, attempted to render the fallback card, and threw an uncaught `ReferenceError: RefreshCw is not defined`, crashing the entire React render tree to a black screen.
  - Adding `RefreshCw` to imports fixes the standby UI.
- **HTML5 `<video>` Error Latching & Dynamic Remounting**:
  - When `<video>` elements fail with 404 (e.g., when the app opens before Keysight renders), the media pipeline enters an error state and sets `opacity: 0.1` via `onError`. When the render later finishes and `waitingForCrop` triggers, React does not remount the element if the `src` string is unchanged.
  - Binding a dynamic key (`key={`${settings.song}_crop_${cropVideoKey}`}`) and cache-buster query parameter (`&t=${cropVideoKey}`) ensures React re-instantiates a fresh HTML5 video element with opacity 1.0 when the crop pause step triggers.
- **5 Version-Specific Persistent Subtitle Fields**:
  - Subtitles exclusively apply to portrait (9:16) videos, never widescreen videos.
  - Added 5 distinct persistent subtitle inputs (`subtitle_normal`, `subtitle_slow`, `subtitle_hook`, `subtitle_easy_normal`, `subtitle_easy_slow`) stored in `settings.json` that persist across song selection changes and map precisely to their respective portrait video renders.

---

### 2026-08-11: V3 Portrait Visualizer Restoration, Video Tearing Fixes, and Multi-Core Performance
- **V3 Portrait Audio Visualizer Engine Restoration**:
  - Restored full V3 portrait audio visualizer in `audio_visualizer.py`: spline waveform interpolation across 88 keys (`scipy.interpolate.make_interp_spline`), glowing core baseline (`bl_thick` + `bl_thin`), multi-frame waterfall ribbons with theme color gradients (`wf_bright`, `wf_mid`, `wf_dark`), and note-reactive downward-shooting dust particles.
  - Multi-core chunk rendering (`_render_chunk_worker`) now uses a 20-frame pre-warmup loop (`warmup_start = max(0, start_frame - max_history)`) so note history arrays are fully populated at chunk boundaries, completely eliminating visible seams across chunks.
- **Visualizer Mask Alignment**:
  - Portrait visualizer masks (`2560x500`) are overlaid at `overlay=x=0:y=0` in `video_generator.py` at the very top edge of the 9:16 screen.
- **NVENC Video Distortions & Multi-Threaded CPU Encoding**:
  - Complex multi-layered FFmpeg filtergraphs with alpha transparency channels (`colorchannelmixer` + `overlay`) and `drawtext` produce internal pixel formats that cause tearing/macroblock corruption when fed directly into NVENC.
  - Adding explicit `setsar=1,format=yuv420p` to the final filtergraph and switching video encoding to multi-threaded CPU `libx264 -preset veryfast -crf 18 -pix_fmt yuv420p` completely eliminates all visual artifacts and distortions while maintaining fast render speeds (~80 FPS).
- **Real-Time Continuous Multi-Core Visualizer Progress Monitoring**:
  - Previously, `ProcessPoolExecutor` with `as_completed` only yielded when full worker chunks finished (resulting in silence for 10-12 minutes before jumping from 0% to 100%).
  - Implemented real-time atomic progress arrays via `multiprocessing.Manager().Array` and an asynchronous monitoring thread in `audio_visualizer.py`. Progress updates (`VIS_PROGRESS:X% (frames, FPS, remaining time)`) are now broadcast continuously every second to the App UI and WebSocket ticker.
- **HandBrake / Keysight RAW 90%+ H.265 Compression**:
  - Upgraded `handbrake_bot.py` with `is_already_compressed` using ffprobe to check stream codec (`hevc`/`h265`). Multi-threaded `libx265 -preset veryfast -crf 22 -tag:v hvc1` reliably compresses Keysight raw captures from ~1.1 GB down to ~95 MB (91-92% reduction).
- **Temp Directory Hygiene**:
  - All intermediate processing files (audio extraction, mask chunks, text draw files, metronome wavs) are now strictly created inside `tools/temp/` and automatically deleted upon job completion.

---

### 2026-09-10: Facebook Graph API Engagement Sync, Mobile Checkout Auto-Address, and Full Arrangement Migration
- **Facebook Graph API Interaction Extraction**:
  - `fb_sync.py` previously queried video objects solely for `views` and defaulted likes/comments to 0, which caused Facebook metrics to display zero interactions in analytics charts despite millions of views.
  - Fix: Augmented Graph API fields to include `likes.summary(true)` and `comments.summary(true)`. The total count is extracted from `data.get('likes', {}).get('summary', {}).get('total_count', 0)` and `comments.get('summary', {}).get('total_count', 0)`. Synced 24k+ likes and 214 comments across the catalog.
- **Stripe Mobile Checkout Conversion (Auto Billing Address)**:
  - Setting `billing_address_collection="required"` on Stripe Checkout sessions caused massive mobile checkout drop-offs because users on smartphones abandoned when required to type full street addresses and postal codes.
  - Switching to `billing_address_collection="auto"` in `routes_public.py` enables seamless 1-tap Apple Pay, Google Pay, and Link payments without manual address inputs.
- **Purge of `viral_part` Database Dependency**:
  - Confirmed that backend routes and database schemas do not restrict queries via `WHERE format = ...`. Ingestion routes default to `format = "full_arrangement"` with 5-video-split generation (Teaser + Normal/Slow + Easy Normal/Slow).
- **Multi-State Catalog Preservation in Ingestion**:
  - Automated sync operations must explicitly preserve custom business state keys (`arrangemeUrl`, `isArrangeMe`, `paymentsDisabled`, `pinned`) in `songs.json` so automated queue runs do not accidentally overwrite manual licensing flags.
- **Dual Difficulty Catalog Schema & Dynamic Currency**:
  - Single records for dual-difficulty songs use `difficulty: "Original / Easy"` with `hasEasy: true`, `easyPrice`, and `easyStripePriceId`.
  - The public catalog endpoint (`/api/public/songs`) must convert currency symbols for both primary `price` and `easyPrice` fields dynamically based on the client's detected IP geolocation.
- [x] English Error Logging Standard:
  - All backend loggers, exception handlers, and API error response payloads must consistently use English to maintain clean diagnostics across local and remote server environments.

---

### 2026-09-17: AI Operations Agent Autonomy, SQLite Locks, and Windows SSH Quoting
- **SQLite DDL Locks on Uncommitted Read Cursors**:
  - In Python's `sqlite3`, iterating a `cursor.execute("SELECT ...")` without an explicit transaction commit leaves a shared read lock open on the database file. Any subsequent DDL statement attempts to acquire an exclusive lock and immediately times out under Windows.
  - Always invoke `conn.commit()` immediately after read queries and before executing schema DDL (`CREATE TABLE IF NOT EXISTS`) or inserting external synchronization records. Set `timeout=30.0` on connection creation.
- **Windows PowerShell / CMD SSH Nested Quote Corruption**:
  - Passing SSH one-liners executing remote Python scripts (`python3 -c "import sqlite3; ..."`) via `subprocess.run(..., shell=True)` on Windows corrupts nested quotes.
  - Never invoke remote Python one-liners with complex quoting via `shell=True`. Pass SSH commands as explicit argument lists `["ssh", "-i", key_path, host, remote_script]` or use minimal SQL statements (`sqlite3 -separator '|' queue.db "SELECT ..."`), processing and filtering the resulting data locally in Python.
- **Production VM API Authentication Headers**:
  - Internal proxies and agents must always inject both `x-admin-passcode: 579110` and `X-Meloscribe-Key` from `api_key.txt` when querying `https://api.meloscribe.dev/api/public/suggestions`.

---

### 2026-09-21: Purchase Email Template Streamlining, Button Placement & PS Feedback Trigger
- **Above-The-Fold Action Optimization**:
  - Eliminated redundant action instruction lines ("Click the button below to download your sheet music...") to move the primary CTA button directly below the intro body sentence (`is ready to practice:` / `... steht bereit zum Üben:`).
  - Placed the permanent link reminder directly beneath the button (`This download link is permanent – you can access it anytime.` / `Dieser Download-Link ist dauerhaft gültig – du kannst jederzeit darauf zugreifen.`).
  - Added dedicated post-scriptum feedback trigger (`PS: Any issues with the files, or ideas to improve? Just hit reply – every feedback helps!`) across all supported locales (EN, DE, FR, ES, IT).
  - Sanitized RFC 2046 plaintext extraction by stripping HTML tags from body text and explicit `reply_to: info@meloscribe.dev`.
  - Purged redundant top logo table and slogan box from HTML email body to display main content directly at the top.

---

### 2026-09-22: Dynamic Catalog Trending Calculation & Cover Asset Pipeline
- **Stale Cover Variant Sync in `upload_bot.py`**:
  - `upload_bot.py` previously only copied `_clean.jpg` to `website/public/covers/`, leaving stale `_wide.jpg` and other variant files in place. The video preview modal (`PaddleModal.tsx`) uses `_wide.jpg`, causing new uploads to display old cover artwork.
  - Fix: Updated `upload_bot.py` to copy all matching cover variations (`clean`, `wide`, standard, and `{clean_name}*.jpg`) on upload.
- **Seasonal Holiday Track Suppression & Catalog Sorting Alignment**:
  - Out of season holiday tracks (*Carol of the Bells*, *Silent Night*) had accumulated views from the previous winter. Without seasonal calendar gating, they can monopolize trending spots outside Christmas despite 0 sales.
  - Fix: Added holiday keyword check (`carol of the bells`, `silent night`, `god rest ye merry`, `we wish you a merry xmas`). Outside of months 11 and 12, holiday arrangements require at least 1 recent purchase to trend.
  - Result: Active non-holiday viral songs (*Golden Brown* with +18.4k views in 30d) qualify for the #3 spot alongside *River Flows in You* and *Sweetest Rain*.
  - Frontend now displays all 3 trending cards on desktop with flame badges (`md:grid-cols-3`) and top 2 on mobile (`grid-cols-2`), with sorting on `/sheets` defaulting to `trending`.
- **Teaser vs Best Part Labeling & Dynamic AI Hashtags:**
  - *Root Cause:* Hook/teaser uploads were appending `" Teaser"` to labels and ntfy notifications. AI hashtags were bypassed when `ai_captions_enabled: false`, and `content_brain.py` prompt had rigid static tags.
  - *Fix:* Replaced `" Teaser"` with `" (Best Part)"` across all platforms and ntfy push headers. Decoupled `ai_hashtags_enabled` to generate and inject dynamic song/genre-specific tags into templates even when standard captions are used. Updated models to `gemini-2.5-flash` and `gemini-flash-latest`.

---

### 2026-09-23: Adblock Evasion, Neutral Endpoint Routing & PUBLIC_ROUTES Whitelisting
- **Adblock Heuristics Blocking Outbound Logging:**
  - Endpoints containing `affiliate`, `track-click`, or `ad-click` trigger network-level blocking (`ERR_BLOCKED_BY_CLIENT`) in Brave Shields, uBlock Origin, and AdGuard for 40–50% of visitors.
  - Solution: Expose neutral `POST /api/events/outbound` and `GET /api/events/outbound/stats` alongside legacy `/api/public/affiliate/*` routes.
- **Security Middleware Trap for New Public Endpoints:**
  - On Linux/Production, `main.py` enforces a `security_middleware` checking `X-Meloscribe-Key` unless the route prefix is listed in `PUBLIC_ROUTES`.
  - Introducing any new public endpoint under a new root path (such as `/api/events`) requires adding `"/api/events"` to `PUBLIC_ROUTES` in `main.py`; otherwise, valid public client beacons will be rejected with 500/403.
- **Flexible Payload Schema:**
  - `OutboundEventRequest` accepts both `{ target, source }` and `{ partner, placement, partnerId }` seamlessly mapping to `affiliate_clicks` in `analytics.db`.

---

### 2026-10-12: ArrangeMe Fallback to Direct Stripe Catalog & Customer Fulfillment ZIPs
- **Catalog State Reset in `upload_bot.py`**:
  - Updating a song previously marked with `isArrangeMe: true` requires an explicit `else:` branch in `add_song_to_website()` to set `isArrangeMe = False` and `paymentsDisabled = False`, clearing `arrangemeUrl` and unblocking Stripe direct checkout on the website frontend.
- **Customer Fulfillment ZIP Packages on Cloudflare R2**:
  - Direct downloads via `routes_public.py` support both individual files (`.pdf`, `.mid`, `.mp4`) and a full `.zip` (`{Song} Full Package.zip`). For direct website arrangements, pre-building and uploading `{Song} Full Package.zip` to R2 guarantees complete customer fulfillment.
