import os
import sys
import re
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import google.generativeai as genai

logger = logging.getLogger("meloscribe.content_brain")

SETTINGS_FILE = Path(__file__).parent / "settings.json"

BANNED_WORDS = [
    # Explicit user ban list (LinkedIn PR & marketing fluff)
    "authentic", "authenticity", "phrasing", "melodic flow", "dynamic touch",
    "capturing", "bringing out", "iconic", "rendition", "precise", "precision",
    "raw energy", "resonate", "resonates", "mastery", "tapestry",
    # Classic AI marketing buzzwords
    "dive into", "delve", "embark", "journey", "elevate", "unleash",
    "testament", "symphony", "blend", "seamless", "meticulously",
    "enchanting", "captivating", "breathtaking", "mesmerizing", "flawless",
    "stunning", "exquisite", "delight", "showcase", "showcasing",
    "masterpiece", "captures", "original phrasing", "piano touch"
]

BLACKLIST_PATTERNS = [
    r"(?i)\bwait for the drop\b",
    r"(?i)\bwait for it\b",
    r"(?i)\bwait for the left hand\b",
    r"(?i)\byou won'?t believe\b",
    r"(?i)\bwatch till the end\b",
    r"(?i)\binsane piano skill\b",
    r"(?i)\bmind[- ]blowing\b",
    r"(?i)\bunreal\b",
    r"(?i)\bviral\b",
    r"(?i)\bor\s+suggest\s+a\s+song\b"
]

def load_settings() -> Dict[str, Any]:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load settings.json: {e}")
    return {}

def get_gemini_key() -> str:
    settings = load_settings()
    key = settings.get("gemini_api_key")
    if key:
        return key.strip()
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

def get_segment_focus_guidance(segment_type: str) -> Dict[str, str]:
    """Returns archetype-specific musician instructions and few-shot pairs."""
    st = (segment_type or "climax").lower()
    if st == "climax":
        return {
            "label": "Chorus / Climax / Power Drop",
            "focus": "High energy, heavy rhythm, left-hand bass stamina, drop impact.",
            "good_sub": "Left hand stamina",
            "good_sub_alt": "Chorus drop",
            "good_cap": "That chorus hits different on piano. Heavy left hand rhythm needs serious stamina. Sheets in bio."
        }
    elif st in ("build_up", "breakdown"):
        return {
            "label": "Breakdown & Build-up / Bridge / Crescendo",
            "focus": "Building tension, rising velocity, bridge transition, preparing the drop.",
            "good_sub": "The build-up hits different",
            "good_sub_alt": "That bridge section",
            "good_cap": "Building the tension before the chorus drops. Probably the most fun part to play. Sheets in bio."
        }
    else:  # melodic_hook, outro, intro
        return {
            "label": "Melodic Hook / Outro / Resolution",
            "focus": "Satisfying chord progression, resolution, voicing, quiet afterglow.",
            "good_sub": "The outro chords",
            "good_sub_alt": "That chord voicing",
            "good_cap": "How the song resolves at the end. That chord voicing is so satisfying. Sheets in bio."
        }

def get_static_fallback(
    format_type: str,
    song_name: str,
    author: str = "",
    platform: str = "tiktok",
    segment_type: str = "climax",
    settings: Optional[Dict[str, Any]] = None
) -> Dict[str, str]:
    """Provides musician-focused static default copy when AI is disabled or offline."""
    if settings is None:
        settings = load_settings()
        
    guidance = get_segment_focus_guidance(segment_type)
    clean_song = song_name.strip()
    
    # 1. Subtitle Fallback (Concise, 2-4 words, real musician hook)
    if format_type == "recycled":
        subtitle = author.strip() if (author and author.strip()) else clean_song
    elif format_type == "hook_teaser":
        subtitle = f"{clean_song} (Hook)"
    elif format_type == "slow_tutorial":
        subtitle = "Slow practice speed"
    elif format_type in ("easy_normal", "easy_slow"):
        subtitle = "Easy piano version"
    else:
        subtitle = f"{clean_song} (Cover)"
    
    # 2. Caption Fallback
    caption = guidance["good_cap"].replace("That chorus", f"The chorus of {clean_song}")
    
    # 3. Hashtags Fallback
    song_clean_tag = re.sub(r'[^a-zA-Z0-9]', '', clean_song).lower()
    hashtags = f"#piano #pianocover #pianotutorial #musictok #{song_clean_tag}"
    
    # 4. Outro Fallback (Strictly Rate this 1-10 👇)
    outro = "Rate this 1-10 👇"
        
    return {
        "subtitle": subtitle,
        "caption": caption,
        "hashtags": hashtags,
        "outro": outro
    }

def sanitize_anti_cringe(text: str, fallback_replacement: str = "") -> str:
    """Removes blacklisted patterns, banned buzzwords, and external URLs."""
    cleaned = text
    for pat in BLACKLIST_PATTERNS:
        cleaned = re.sub(pat, "", cleaned)
    
    # Check for banned marketing buzzwords
    for bw in BANNED_WORDS:
        bw_pattern = rf"(?i)\b{re.escape(bw)}\b"
        if re.search(bw_pattern, cleaned):
            cleaned = re.sub(bw_pattern, "", cleaned)
            
    # Replace external URLs with 'Sheets in bio'
    cleaned = re.sub(r'https?://\S+', 'Sheets in bio', cleaned)
    cleaned = re.sub(r'\b[a-zA-Z0-9.-]+\.(?:dev|com|org|net|io)\b', 'Sheets in bio', cleaned)
    
    # Clean up whitespace
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    
    # Strip any trailing 'or suggest a song'
    cleaned = re.sub(r'(?i)\s+or\s+suggest\s+a\s+song', '', cleaned).strip()
    
    if not cleaned and fallback_replacement:
        return fallback_replacement
    return cleaned

def contains_banned_words(text: str) -> bool:
    """Returns True if text contains any forbidden PR or marketing fluff word."""
    for bw in BANNED_WORDS:
        if re.search(rf"(?i)\b{re.escape(bw)}\b", text):
            return True
    return False

def generate_content_copy(
    format_type: str,
    song_name: str,
    author: str = "",
    technical_focus: str = "",
    platform: str = "tiktok",
    segment_type: str = "climax",
    force_ai: bool = False
) -> Dict[str, str]:
    """
    Generates authentic, musician-level copy for short-form video (TikTok, Reels, Shorts).
    Zero PR buzzwords, strict negative constraints, archetype-aware prompting.
    """
    settings = load_settings()
    fallback = get_static_fallback(format_type, song_name, author, platform, segment_type, settings)
    
    ai_sub = settings.get("ai_subtitles_enabled", True) or force_ai
    ai_cap = settings.get("ai_captions_enabled", True) or force_ai
    ai_hash = settings.get("ai_hashtags_enabled", True) or force_ai
    ai_outro = settings.get("ai_outro_enabled", True) or force_ai
    
    if not (ai_sub or ai_cap or ai_hash or ai_outro):
        return fallback
        
    api_key = get_gemini_key()
    if not api_key:
        logger.info("No Gemini API key found; returning static fallback copy.")
        return fallback
        
    guidance = get_segment_focus_guidance(segment_type)
    banned_list_str = ", ".join(f'"{w}"' for w in BANNED_WORDS)
    clean_song_tag = re.sub(r'[^a-zA-Z0-9]', '', song_name).lower()
    clean_author_tag = re.sub(r'[^a-zA-Z0-9]', '', author).lower() if author else "pianist"
    
    prompt = f"""Du bist ein echter Pianist und Content Creator auf TikTok, Instagram Reels und YouTube Shorts.
Deine Texte klingen wie ein echter Musiker, der gerade vom Klavier aufgestanden ist: direkt, kernig, beobachtend, ehrlich.
KEIN Marketing-Geschwurbel, KEINE LinkedIn-Pressemitteilung einer städtischen Musikschule, KEIN Werbeagentur-Text.

PROFIL DES CLIPS:
- Format: {format_type}
- Song: {song_name}
- Artist: {author or 'Unknown'}
- Segment-Typ: {guidance['label']}
- Musiker-Fokus: {technical_focus or guidance['focus']}
- Plattform: {platform}

STRIKTE NEGATIVE CONSTRAINTS (VERBOTENE WÖRTER):
Folgende Wörter und Phrasen sind ABSOLUT VERBOTEN. Wenn dein Output eines dieser Wörter enthält, schlägt er fehl:
{banned_list_str}

FEW-SHOT GUIDELINES (GOOD VS BAD):
❌ BAD Subtitle: "Original phrasing flow" | "Dynamic piano touch" | "Authentic feel"
✅ GOOD Subtitle: "{song_name} (Chorus)" | "{guidance['good_sub']}" | "{guidance['good_sub_alt']}"

❌ BAD Caption: "Bringing out the authentic feel of {song_name} with precise original phrasing and melodic flow..."
✅ GOOD Caption: "{guidance['good_cap']}"

❌ BAD Caption: "Capturing the iconic energy of {song_name} with a dynamic touch for an authentic rendition..."
✅ GOOD Caption: "Building the tension before the chorus drops. Probably the most fun part to play. Sheets in bio."

REGELN FÜR DIE AUSGABE:
1. SUBTITLE (9:16 Video On-Screen Hook):
   - Maximal 2 bis 4 Worte!
   - Visueller Anker für 0,3s Scroll-Stopp.
   - Entweder der Song-Part (z.B. "{song_name} (Chorus)") oder eine spieltechnische Beobachtung (z.B. "{guidance['good_sub']}").
2. CAPTION:
   - 1 bis maximal 2 kurze Sätze aus der Perspektive des Pianisten (Spielgefühl, linke Hand, Tempo, Drop).
   - MUSS immer mit "Sheets in bio." enden (oder "Sheets in bio if you want to learn it.").
   - Keine Floskeln, keine URLs.
3. OUTRO:
   - Immer exakt: "Rate this 1-10 👇" (OHNE "or suggest a song").
4. HASHTAGS:
   - 4-6 relevante Hashtags: #{clean_song_tag} #{clean_author_tag} #pianocover #pianotutorial #musictok #piano

Antworte AUSSCHLIESSLICH im folgenden JSON-Format ohne Markdown-Codeblöcke:
{{
  "subtitle": "...",
  "caption": "...",
  "hashtags": "...",
  "outro": "Rate this 1-10 👇"
}}
"""

    genai.configure(api_key=api_key)
    res_text = ""
    for model_name in ['models/gemini-3.8-flash', 'models/gemini-2.5-flash']:
        try:
            m = genai.GenerativeModel(model_name)
            resp = m.generate_content(prompt)
            if resp and resp.text:
                res_text = resp.text.strip()
                break
        except Exception as e:
            logger.warning(f"Model {model_name} failed: {e}")
            continue
            
    if not res_text:
        return fallback

    # Parse JSON
    try:
        clean_json = re.sub(r'^```json\s*', '', res_text, flags=re.MULTILINE)
        clean_json = re.sub(r'^```\s*', '', clean_json, flags=re.MULTILINE)
        data = json.loads(clean_json.strip())
        
        raw_sub = data.get("subtitle", "")
        raw_cap = data.get("caption", "")
        raw_hash = data.get("hashtags", "")
        raw_outro = data.get("outro", "")
        
        # Hard check on banned words
        if contains_banned_words(raw_sub):
            logger.warning(f"Banned word detected in subtitle '{raw_sub}', reverting to guidance fallback.")
            raw_sub = guidance["good_sub"]
            
        if contains_banned_words(raw_cap):
            logger.warning(f"Banned word detected in caption '{raw_cap}', reverting to guidance fallback.")
            raw_cap = guidance["good_cap"]
            
        out = {}
        out["subtitle"] = sanitize_anti_cringe(raw_sub, fallback["subtitle"]) if ai_sub and raw_sub else fallback["subtitle"]
        out["caption"] = sanitize_anti_cringe(raw_cap, fallback["caption"]) if ai_cap and raw_cap else fallback["caption"]
        out["hashtags"] = sanitize_anti_cringe(raw_hash, fallback["hashtags"]) if ai_hash and raw_hash else fallback["hashtags"]
        
        # Outro is strictly hardcoded to the clean prompt
        out["outro"] = "Rate this 1-10 👇" if (ai_outro and raw_outro) else fallback["outro"]
        
        # Ensure subtitle is concise (< 40 chars and max 5 words)
        words = out["subtitle"].split()
        if len(words) > 5:
            out["subtitle"] = " ".join(words[:4])
        if len(out["subtitle"]) > 35:
            out["subtitle"] = out["subtitle"][:35].strip()
            
        return out
    except Exception as e:
        logger.error(f"Error parsing Gemini copy output: {e}. Raw response: {res_text[:100]}")
        return fallback

if __name__ == "__main__":
    import sys
    print("Testing Content Brain with 3 segments...")
    for seg in ["climax", "build_up", "melodic_hook"]:
        res = generate_content_copy("recycled", "Believer", "Imagine Dragons", segment_type=seg, force_ai=True)
        print(f"\n--- Segment: {seg} ---")
        print("Subtitle:", res["subtitle"])
        print("Caption :", res["caption"])
        print("Outro   :", res["outro"])
        print("Hashtags:", res["hashtags"])
