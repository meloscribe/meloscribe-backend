import google.generativeai as genai
import sqlite3
import json
from pathlib import Path
import datetime
from trend_engine import get_all_trends

# Set up Gemini
GEMINI_API_KEY = "AIzaSyDheMAcQqcpbUZjKVPitl6fqXVDMUpwzX8"
genai.configure(api_key=GEMINI_API_KEY)

# Fallback to gemini-2.5-flash as gemini-3.1-pro-preview has a quota limit of 0 on the free tier
model = genai.GenerativeModel('models/gemini-2.5-flash')

DB_PATH = Path(__file__).parent / "analytics.db"

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
    
    conn.close()
    return {
        "total_lifetime_views": total_views,
        "format_performance": format_stats,
        "length_performance": length_stats,
        "recent_top_songs": recent_top,
        "current_todo_list": todos,
        "dismissed_suggestions": dismissed
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
