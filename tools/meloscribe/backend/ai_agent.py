import google.generativeai as genai
import sqlite3
import json
from pathlib import Path
import datetime
from trend_engine import get_all_trends

# Set up Gemini
def get_gemini_key():
    import os
    try:
        # Check settings.json in the same folder or parent folder
        for p in (Path(__file__).parent / "settings.json", Path(__file__).parent.parent / "settings.json"):
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                key = settings.get("gemini_api_key")
                if key:
                    return key
    except Exception:
        pass
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

GEMINI_API_KEY = get_gemini_key()
genai.configure(api_key=GEMINI_API_KEY)

# Fallback to gemini-2.5-flash as gemini-3.1-pro-preview has a quota limit of 0 on the free tier
model = genai.GenerativeModel('models/gemini-2.5-flash')

DB_PATH = Path(__file__).parent / "analytics.db"

def get_settings():
    try:
        for p in (Path(__file__).parent / "settings.json", Path(__file__).parent.parent / "settings.json"):
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
    except Exception:
        pass
    return {}

def fetch_recent_data():
    """Fetch the most critical data from the last 14 days and overall top metrics."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    fourteen_days_ago = (datetime.datetime.now() - datetime.timedelta(days=14)).isoformat()
    
    # Best format
    format_stats = [dict(r) for r in c.execute("SELECT format, AVG(views) as avg_views FROM videos GROUP BY format").fetchall()]
    
    # Best video length
    length_stats = [dict(r) for r in c.execute("SELECT CASE WHEN duration_sec < 61 THEN 'Short' ELSE 'Long' END as length, AVG(views) as avg_views FROM videos GROUP BY length").fetchall()]
    
    # Total Views
    total_views = c.execute("SELECT SUM(views) FROM videos").fetchone()[0] or 0
    
    # Recent top songs (last 14 days publish)
    recent_top = [dict(r) for r in c.execute("SELECT song_name, SUM(views) as views FROM videos WHERE publish_date >= ? GROUP BY song_name ORDER BY views DESC LIMIT 3", (fourteen_days_ago,)).fetchall()]
    
    # Current Todos
    todos = [dict(r) for r in c.execute("SELECT song_name, status FROM todos WHERE status='pending'").fetchall()]
    # Get dismissed suggestions to avoid recommending them again
    dismissed = [r["song_name"] for r in c.execute("SELECT song_name FROM dismissed_suggestions").fetchall()] if "dismissed_suggestions" in [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()] else []
    
    # Paddle purchases metrics
    purchases_stats = []
    try:
        purchases_stats = [dict(r) for r in c.execute("SELECT song_name, COUNT(*) as sales_count, SUM(CAST(amount as REAL)) as total_sales FROM purchases GROUP BY song_name").fetchall()]
    except Exception as e:
        print(f"[AI Agent] Failed to fetch purchases stats: {e}")

    # Overall revenue summary
    revenue_summary = {"total_amount": 0.0, "transactions_count": 0}
    try:
        rev_row = c.execute("SELECT SUM(CAST(amount as REAL)) as total, COUNT(*) as count FROM revenue").fetchone()
        if rev_row and rev_row["total"] is not None:
            revenue_summary = {"total_amount": rev_row["total"], "transactions_count": rev_row["count"]}
    except Exception as e:
        print(f"[AI Agent] Failed to fetch revenue stats: {e}")

    # Channel insights (latest followers & profile views for platform views)
    channel_insights = []
    try:
        channel_insights = [dict(r) for r in c.execute("""
            SELECT platform, followers, profile_views, website_clicks, date 
            FROM channel_insights 
            WHERE (platform, date) IN (
                SELECT platform, MAX(date) FROM channel_insights GROUP BY platform
            )
        """).fetchall()]
    except Exception as e:
        print(f"[AI Agent] Failed to fetch channel insights: {e}")
        
    # --- Live remote metrics retrieval in local mode (Windows) ---
    import platform
    import urllib.request
    import urllib.error
    if platform.system() == "Windows":
        settings = get_settings()
        api_key = settings.get("server_api_key")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        if api_key:
            headers['X-Meloscribe-Key'] = api_key
            
        try:
            req = urllib.request.Request(
                "https://api.meloscribe.dev/api/analytics?range=30d",
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=5.0) as response:
                remote_analytics = json.loads(response.read().decode('utf-8'))
                if "platformBreakdown" in remote_analytics:
                    remote_insights = []
                    for pb in remote_analytics["platformBreakdown"]:
                        remote_insights.append({
                            "platform": pb.get("platform"),
                            "followers": pb.get("followers", 0) or pb.get("likes", 0),
                            "profile_views": pb.get("views", 0),
                            "website_clicks": pb.get("saves", 0),
                            "date": datetime.date.today().isoformat() if hasattr(datetime, 'date') else datetime.datetime.now().date().isoformat()
                        })
                    if remote_insights:
                        channel_insights = remote_insights
                if "totals" in remote_analytics and remote_analytics["totals"]:
                    t_views = remote_analytics["totals"].get("v")
                    if t_views is not None:
                        total_views = t_views
        except Exception as err:
            print(f"[AI Agent] Warning: Failed to fetch live analytics from VM: {err}")
            
        try:
            req = urllib.request.Request(
                "https://api.meloscribe.dev/api/paddle/sales",
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=5.0) as response:
                sales = json.loads(response.read().decode('utf-8'))
                if isinstance(sales, list):
                    song_sales = {}
                    total_rev = 0.0
                    for sale in sales:
                        s_name = sale.get("song_name") or "Unknown"
                        amt = float(sale.get("amount") or 0.0)
                        total_rev += amt
                        if s_name not in song_sales:
                            song_sales[s_name] = {"song_name": s_name, "sales_count": 0, "total_sales": 0.0}
                        song_sales[s_name]["sales_count"] += 1
                        song_sales[s_name]["total_sales"] += amt
                    
                    purchases_stats = list(song_sales.values())
                    revenue_summary = {
                        "total_amount": total_rev,
                        "transactions_count": len(sales)
                    }
        except Exception as err:
            print(f"[AI Agent] Warning: Failed to fetch live sales from VM: {err}")

    conn.close()
    return {
        "total_lifetime_views": total_views,
        "format_performance": format_stats,
        "length_performance": length_stats,
        "recent_top_songs": recent_top,
        "current_todo_list": todos,
        "dismissed_suggestions": dismissed,
        "purchases_stats": purchases_stats,
        "revenue_summary": revenue_summary,
        "channel_insights": channel_insights
    }

def generate_daily_briefing():
    """Generate a new daily briefing using trends and local analytics."""
    trends = get_all_trends()
    local_data = fetch_recent_data()
    
    prompt = f"""
Rolle: Du bist der knallharte Daten-Analyst für „MeloScribe“. Dein Ziel ist es, Tobias ein genaues Status-Update zu geben, wie sein Content gerade auf allen Plattformen performt.

Geschäftsmodell:
- Produkt: Verkauf von Klavier-Noten (Sheets) und MIDI-Dateien über Ko-fi.
- Marketing: Organische Reichweite durch Piano-Tutorials und Covers (YouTube, Instagram, TikTok, Facebook). Tobias macht immer Original + Tutorial.

Daten-Grundlage (Tobias's local performance data):
{json.dumps(local_data, indent=2)}

Aktuelle globale Trends (Spotify/Last.fm, YouTube Music, Google Trends):
{json.dumps(trends, indent=2)}

Deine Aufgabe:
Erstelle ein Status-Update (Briefing) auf DEUTSCH.
- VERBOTEN sind allgemeine Tipps wie "Mache mehr Shorts" oder "Shorts funktionieren gut" oder "Achte auf die Hook".
- VERBOTEN sind Sätze wie "Priorisiere dieses Format". 
- PFLICHT: Nenne exakt, welche Songs (aus den lokalen Daten!) aktuell am stärksten wachsen oder die meisten Views/Saves haben.
- PFLICHT: Vergleiche die Plattformen (z.B. "Song X geht gerade auf TikTok durch die Decke, aber auf YouTube passiert nichts").
- PFLICHT: Nenne 10 GANZ KONKRETE neue Songs aus den globalen Trends, die perfekt zur Zielgruppe (Klavierspieler Pop/Film) passen. Schlage NIEMALS Songs vor, die in `dismissed_suggestions` oder `current_todo_list` stehen!

Output MUST be valid JSON matching this exact structure:
{{
  "recommendation": "Eine kurze, sehr spezifische Status-Zusammenfassung (2-3 Sätze): Welcher alte Song läuft gerade am besten? Welcher Trend-Song MUSS heute gespielt werden?",
  "analysis": "Detaillierte Analyse (1-2 Absätze). Verknüpfe harte Zahlen aus seinen Analytics mit den aktuellen Trends. Erkläre genau, wo aktuell das Wachstum herkommt. Nenne konkrete Song-Beispiele aus seinen Daten.",
  "suggested_songs": ["Songname - Autor", "Songname - Autor", ...] // EXAKT 10 SONGS!
}}
WICHTIG: Die suggested_songs MÜSSEN im Format 'Songname - Autor' sein.
Only output JSON. No markdown wrappers.
"""
    try:
        response = model.generate_content(prompt)
        raw_text = response.text
        
        # Strip markdown code fences if present
        text = raw_text.replace("```json", "").replace("```", "").strip()
        
        # For thinking models: extract only the JSON object from the response
        # The thinking model may prefix with internal reasoning
        json_start = text.find('{')
        json_end = text.rfind('}')
        if json_start != -1 and json_end != -1:
            text = text[json_start:json_end + 1]
        
        data = json.loads(text)
        
        # Validate expected keys
        if "recommendation" not in data or "analysis" not in data:
            print(f"[AI Agent] Warning: Response missing expected keys. Raw: {raw_text[:200]}")
            return None
        
        if "suggested_songs" not in data:
            data["suggested_songs"] = []
        
        # Save to DB
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO ai_reports (report_date, recommendation_text, analysis_text, suggested_songs) VALUES (?, ?, ?, ?)",
                  (datetime.datetime.now().isoformat(), data["recommendation"], data["analysis"], json.dumps(data["suggested_songs"])))
        conn.commit()
        conn.close()
        
        return data
    except json.JSONDecodeError as e:
        print(f"[AI Agent] JSON parse error: {e}")
        print(f"[AI Agent] Raw response text: {raw_text[:500] if 'raw_text' in dir() else 'N/A'}")
        raise Exception(f"JSON Parse Error: {str(e)} | Raw text: {raw_text[:200]}")
    except Exception as e:
        print(f"[AI Agent] Error generating daily briefing: {e}")
        raise e

def get_latest_briefing():
    """Get the latest briefing from DB, or generate one if older than 24h."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM ai_reports ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    
    generate_new = False
    if not row:
        generate_new = True
    else:
        report_date = datetime.datetime.fromisoformat(row["report_date"])
        if (datetime.datetime.now() - report_date).total_seconds() > 86400: # 24 hours
            generate_new = True
            
    if generate_new:
        try:
            new_briefing = generate_daily_briefing()
            if new_briefing:
                return new_briefing
        except Exception as e:
            print(f"[AI Agent] Failed to generate new briefing, falling back to cache if available. Error: {e}")
            if not row:
                # We have absolutely nothing to show
                raise e
            # If we do have an old row, we will just return it below instead of crashing
    
    return {
        "recommendation": row["recommendation_text"],
        "analysis": row["analysis_text"],
        "suggested_songs": json.loads(row["suggested_songs"])
    }

def chat_with_agent(message: str, history: list):
    """Chat with the agent using history."""
    formatted_history = []
    for msg in history:
        formatted_history.append({"role": "user" if msg["role"] == "user" else "model", "parts": [msg["content"]]})
        
    chat = model.start_chat(history=formatted_history)
    
    trends = get_all_trends()
    local_data = fetch_recent_data()
    
    identity_block = f"""
Rolle: Du bist der knallharte Daten-Analyst für „MeloScribe“. 
Geschäftsmodell: Verkauf von Klavier-Noten via Ko-fi durch Organische Reichweite (Shorts/Reels).

Kommunikations-Stil:
- Sprich direkt, ehrlich und menschlich auf DEUTSCH.
- Gib KEINE allgemeinen Ratschläge (wie "Achte auf Retention" oder "Mache mehr Shorts").
- Beziehe dich IMMER auf seine aktuellen lokalen Daten. Wenn er fragt "Wie läufts?", nenne exakte View-Zahlen und vergleiche Plattformen.
- Wenn du Songs vorschlägst, formatiere sie IMMER als: * Songname - Autor

Tobias's Data: {json.dumps(local_data)}
Trends: {json.dumps(trends)}
"""
    
    context = f"{identity_block}\n\nTobias's message: {message}"
    
    response = chat.send_message(context)
    return response.text

if __name__ == "__main__":
    print(json.dumps(get_latest_briefing(), indent=2))
