# Antigravity Music - Workflow & Automation Guide

Dieses Dokument ist das architektonische Gehirn des **Antigravity Music Bot** Projekts.
Das Projekt automatisiert die Produktion und Distribution von professionellen **Piano-Tutorial Videos**. Es nimmt die musikalischen Roh-Daten aus der DAW (Cakewalk) und erzeugt vollautomatisch fertig geschnittene Tutorials in Keysight, TikTok Formate, Canva stylishe Thumbnail Covers, packt Notenblätter in ZIPs und lädt sie mithilfe eines Headless Browsers unbemerkt auf Social Media und Ko-Fi Shops hoch. Zusätzlich analysiert eine **Analytics Engine** die Song-Metadaten und korreliert sie mit echten Social-Media-Insights.

**Zweck:** Neue KI-Assistenten können dieses Dokument lesen, um zu jedem Zeitpunkt sofort zu wissen:
- Was ist das Projektgebiet?
- Wo sich Dateien zielgenau befinden.
- Wie die extrem strikten Namenskonventionen lauten.
- Wie der Ablauf und die GUI Tools funktionieren.

---

## 1. Strenge Globale Namenskonvention (WICHTIG!)
Um das gesamte Pipeline-Ökosystem modular und fehlerfrei zu halten, gelten für **absolut alle generierten Module (Covers, Videos, Audios)** exakt zwei statische Namensformate:
1. Das **Normale (schnelle)** Asset heißt **immer**: `[Songname].[Dateiendung]` (z.B. `Sweetest Rain.mp4`, `Sweetest Rain.jpg`)
2. Das **Tutorial (langsame)** Asset heißt **immer**: `[Songname] slow.[Dateiendung]` (z.B. `Sweetest Rain slow.mp4`, `Sweetest Rain slow.jpg`)

*Es gibt **keine** Ausnahmen wie "Cover.jpg", "tutorial.mp4" oder ähnliches. Diese Konvention stellt sicher, dass alle Upload-Bots instinktiv ohne Metadaten oder API-Calls wissen, welches File sie aus welchem Modul ziehen müssen! Dateisuchen müssen in Python immer Case-Insensitive (groß-/klein-ignorierend) geschrieben werden.*

---

## 2. Speicherorte & Verzeichnisse (Die Pipeline)
Die Pipeline ist extrem linear. Ordnernamen und Formate sind statisch:
- **Raw Cakewalk Projekte:** `C:\Cakewalk Projects\[Songname]` (Ein Ordner pro Song)
- **Cakewalk Audio Export (Input Phase 1):** `C:\Cakewalk Projects\[Songname]\Audio Export` (Hier exportiert der Nutzer initial seine rohen `.wav` Dateien)
- **Normalisierte Audios:** `C:\Cakewalk Projects\.Audacity` (Ablageort für die von unserem Python Audio Optimizer generierten -21 LUFS Dateien)
- **Keysight RAW Video Export:** `C:\Dev\meloscribe\Keysight export` (Die massive, hochaufgelöste Render-Ansicht im Querformat aus Keysight inkl. In-Place-Handbrake Komprimierung!)
- **Fertige Social Media Videos:** `C:\Dev\meloscribe\TikToks` (Das Output-Verzeichnis vom Video Editor, fertig für den TikTok / Reels Feed)
- **Generierte Thumbnail Cover:** `C:\Dev\meloscribe\Covers` (Hierhin rendert der Cover Bot seine `[Song].jpg` Dateien)
- **MuseScore Notenblätter:** `C:\Dev\meloscribe\Scores` (Verwaltet im Workspace für Projekt-Sichtbarkeit, automatische Vorlagennutzung und Schutz vor Überschreiben)
- **Fertige Ko-Fi Zip Bundles:** `C:\Dev\meloscribe\packages\[Songname] Full Package.zip`

---

## 3. Die Automatisierungsarchitektur / App Bedienung
Die Steuerung ist modernisiert! Die alte `CustomTkinter`-App wurde durch eine blitzschnelle **Electron Desktop-App** (Frontend: React/Vite in `tools/meloscribe/frontend`, Backend: Python/FastAPI in `tools/meloscribe/backend`) abgelöst. Das UI nutzt ein helles iOS Glassmorphism Design mit Animationshintergrund (Dark Mode konfigurierbar über die Titlebar). Alle Module agieren über asynchrone Websockets.

### Schritt 0: Das menschliche Einspielen
Der User nutzt Cakewalk, spielt Audio live ein, bearbeitet MIDI, PDF und WAV Tracks und platziert `[Song].wav`, `[Song] slow.wav` sowie die `.mid` Derivate roh im `Cakewalk Projects` Verzeichnis.

### Phase 1: Die Heavy-Lifting Datenaufbereitung
Der Workflow-Button in der Master-Automation-Tab führt alles aus:
1. **1. Audio Normalizer:** Liest RAW WAVs ein und levellisiert deren Lautstärke unhörbar exakt auf YouTube-Vorgaben (-21 LUFS), outputtet normierte WAV in den `.Audacity` Ordner.
2. **2. Keysight Bot (`keysight_bot.py`):** Visuelle KI Macro (PyAutoGUI + OpenCV), wehrt Popups ab, tippt Pfade ein und rendert die Noten/Licht-Animation im Querformat bei 60 FPS vollautomatisch durch. Output landet im `Keysight export`.
3. **3. Handbrake Optimizer (`handbrake_bot.py`):** Ruft via `subprocess` nativ FFmpeg `libx265` auf! Da die Keysight MP4 Files in die hunderte Gigabyte gehen, komprimiert dieser Schritt die `Keysight export` Dateien vor Ort (in-place) drastisch verlustfrei ein.

### Die AI Intermission (Live Canvas Tuning)
Im `MasterTab.jsx` kann der Nutzer Parameter via React Sliders ändern: `Zoom` (x1.1) und `Shift` (15px) justieren. Ein nativer HTML5 Video Player greift dank desaktvierter `webSecurity` in Electron direkt auf lokales Material zu und visualisiert eine 9:16 TikTok Overlay-Box über der 16:9 MP4 in Echtzeit!

### Phase 2: Die Generierung (Derivate)
Nach Freigabe läuft der Loop weiter:
4. **4. TikTok Editor & Metadaten Extractor (`video_generator.py`):** FFmpeg schneidet das Video (9:16), zentriert exakt auf die Shift-Werte der Intermission, addiert Background Blur und legt Font-Overlays über das Video. **NEU:** Bei jedem Lauf liest das Skript via `mido` die BPM, Taktart und genaue Länge (Duration) aus der MIDI-Datei aus und schreibt sie fehlerfrei und ohne Duplikate (via `INSERT OR REPLACE`) in die `analytics.db` der Intelligence Engine!
5. **5. Cover Generator (`cover_generator.py`):** Bildet native PIL-Zeichenlogik ab. Rendert `[Song].jpg` und `[Song] slow.jpg` in `Covers`.
6. **6. Ko-Fi Packager (`kofi_zipper.py`):** Verpackt alles (Videodateien, MIDI, PDF) in ein Ko-Fi Upload ZIP, sobald Notenblätter verfügbar sind.

### Phase 3: API-Native Distribution & Analytics Hooking
Das Herzstück der Distribution — nun vollständig API-basiert (kein Metricool mehr, außer Ko-Fi alles nativ):
7. **7. YouTube API Upload (`yt_poster.py`):** Lädt das fertige Video direkt über die YouTube Data API v3 hoch, inklusive nativer Zeitplanung via `publishAt`. `condensed=true` → Shorts. `condensed=false` → Long-form mit Thumbnail-Upload. Gibt den finalen `youtu.be`-Link an den Ko-Fi Bot weiter. Duplikat-Erkennung via YouTube Search API.
8. **8. Instagram Reel Upload (`ig_poster.py`):** Nutzt die Instagram Graph API zum Upload von Reels inkl. `scheduled_publish_time` für server-seitiges Scheduling (PC kann aus sein!).
9. **9. Facebook Video/Reel Upload (`fb_poster.py`):** Nutzt die Facebook Graph API. `condensed=true` → Reel via `/video_reels`. `condensed=false` → Reguläres Video via `/videos` mit Thumbnail-Upload. Scheduling via `scheduled_publish_time`.
10. **10. TikTok API Upload (`tiktok_poster.py`):** Nutzt die offizielle TikTok Direct Post API mit Chunked Upload. **Immer als private** gepostet — der Nutzer schaltet manuell auf public um (TikTok-API hat kein natives Scheduling).
11. **11. Threads API Upload (`threads_poster.py`):** Nutzt die Threads Graph API (`graph.threads.net`). Video wird temporär zu file.io hochgeladen (Threads API verlangt eine Public URL), Container wird erstellt und nach Processing publiziert. Long-Lived Token (60 Tage) mit automatischem Refresh.
11. **11. Ko-Fi Web Uploader (`upload_bot.py --mode kofi`):** Playwright-Bot für Ko-Fi Shop (API unterstützt keine Produkt-Uploads). Empfängt den YouTube-Link per `--youtube_url` Argument direkt von Step 7.
12. **12. Analytics Sync (Startup-Background):** Beim Start von `main.py` laufen automatisch `tiktok_sync.py`, `ig_sync.py`, `fb_sync.py` und `yt_sync.py` im Hintergrund. Diese synchronisieren Views, Likes, Shares und Comments aus den jeweiligen Plattform-APIs in die lokale `analytics.db`.
13. **13. Ko-Fi Webhooks (`POST /api/kofi/webhook`):** Ko-Fi sendet bei Donations/Shop Orders automatisch Events an unser Backend, die als Revenue in die `analytics.db` geschrieben werden.
14. **14. Ko-Fi CSV Sync (`kofi_csv_sync.py`):** Playwright-basierter Scraper, der sich mit gespeichertem Cookie (`kofi_cookie.txt`) einloggt, den CSV-Export triggert und alle historischen Sales in die `revenue`-Tabelle importiert. **Song-Name-Normalisierung:** Ko-Fi-Produktnamen wie "Nuvole Bianche all parts" werden automatisch zu "Nuvole Bianche" normalisiert, damit sie 1:1 mit der `videos`-Tabelle matchen. Duplikat-Erkennung via `buyer + amount + song_name`.
15. **15. MeloScribe AI Advisor (`ai_agent.py`):** Gemini-basierter KI-Agent, der alle 24h ein tägliches Briefing generiert (Empfehlung + Deep Analysis + 10 Song-Vorschläge). Nutzt `trend_engine.py` (Last.fm + YouTube Trends + Google Trends via pytrends) und die lokalen Analytics-Daten. Berücksichtigt bereits abgelehnte Vorschläge (`dismissed_suggestions`-Tabelle). Interaktiver Chat für Nachfragen. Todo-Liste für geplante Songs mit automatischer Priorisierung nach historischer Performance.

**Upload Targets im MasterTab:** Pro Plattform ein eigener Toggle (YouTube, Instagram, Facebook, TikTok, Threads, Ko-Fi). Tutorial-Uploads bekommen automatisch einen 10-Minuten-Offset. Pause/Resume-Logik speichert den letzten erfolgreich abgeschlossenen Step.

**UI-Sprache:** Alle UI-Labels und Buttons sind auf Englisch. Nur der AI Agent (Briefings, Analysen, Chat-Antworten) antwortet auf Deutsch, da der Prompt explizit deutsche Analysen generiert.

Alle GUI Prozesse des Frontends besitzen Cancellation Propagations über Popen und WebSocket-Logs ins React UI. Die Sidebar zeigt dauerhaft den Verbindungsstatus aller Plattform-APIs (TikTok, Instagram, YouTube, Facebook, Threads) an.
