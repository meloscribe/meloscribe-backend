import os
import sys
import math
import argparse
import textwrap
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

tools_dir = os.path.dirname(os.path.abspath(__file__))
FFMPEG_EXE = os.path.join(tools_dir, "ffmpeg", "bin", "ffmpeg.exe")
if not os.path.exists(FFMPEG_EXE):
    FFMPEG_EXE = "ffmpeg"

THEME_GLOW_PALETTES = {
    'warm': {
        'glow_rgb': (255, 170, 40),      # Warm Amber / Gold
        'inner_glow': (255, 215, 110),
        'bg_glow': (240, 130, 20),
    },
    'violet': {
        'glow_rgb': (215, 75, 255),      # Neon Violet / Purple
        'inner_glow': (240, 150, 255),
        'bg_glow': (170, 30, 230),
    },
    'purple': {
        'glow_rgb': (215, 75, 255),
        'inner_glow': (240, 150, 255),
        'bg_glow': (170, 30, 230),
    },
    'cold': {
        'glow_rgb': (55, 195, 255),      # Ice Cyan / Electric Blue
        'inner_glow': (160, 235, 255),
        'bg_glow': (20, 140, 240),
    },
    'green': {
        'glow_rgb': (50, 240, 130),      # Emerald / Neon Green
        'inner_glow': (150, 255, 190),
        'bg_glow': (20, 200, 90),
    },
    'platinum': {
        'glow_rgb': (220, 235, 255),     # Silver / Platinum White
        'inner_glow': (255, 255, 255),
        'bg_glow': (170, 190, 220),
    }
}

def ensure_fonts():
    spartan_path = "arialbd.ttf"
    montserrat_path = "arial.ttf"
    return spartan_path, montserrat_path

def draw_text_with_shadow(draw, text, x, y, font, text_color=(255, 255, 255), shadow_color=(0, 0, 0), shadow_offset=(3, 3), align="center", anchor="ma", spacing=60):
    draw.multiline_text((x + shadow_offset[0], y + shadow_offset[1]), text, font=font, fill=shadow_color, align=align, anchor=anchor, spacing=spacing)
    draw.multiline_text((x, y), text, font=font, fill=text_color, align=align, anchor=anchor, spacing=spacing)

def extract_highlight_frame(video_path, num_samples=16):
    import cv2
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if total_frames > 0 else 0
    cap.release()
    
    if duration <= 0:
        return None
        
    timestamps = [duration * (0.25 + 0.55 * (i / (num_samples - 1))) for i in range(num_samples)]
    best_score = -1
    best_frame = None
    
    tmp_dir = os.path.join(tools_dir, "temp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_img = os.path.join(tmp_dir, f"tmp_highlight_{os.getpid()}.jpg")
    
    for t in timestamps:
        cmd = [
            FFMPEG_EXE, '-y', '-ss', f'{t:.2f}', '-i', video_path,
            '-vframes', '1', '-q:v', '2', tmp_img
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(tmp_img):
            frame = cv2.imread(tmp_img)
            if frame is not None:
                h, w, _ = frame.shape
                roi = frame[int(h*0.15):int(h*0.75), int(w*0.10):int(w*0.90)]
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                bright_pixels = np.sum(gray > 50)
                mean_bright = np.mean(gray)
                score = bright_pixels * 0.5 + mean_bright * 80
                if score > best_score:
                    best_score = score
                    best_frame = frame.copy()
    if os.path.exists(tmp_img):
        try:
            os.remove(tmp_img)
        except Exception:
            pass
    return best_frame

def apply_cinematic_contrast_backdrop(base_img, center_y=410, keys_y=790, peak_alpha=160, top_alpha=100):
    """
    Applies a continuous top-down cinematic vignette spanning the entire height from y=0 to keys_y.
    Starts with a gentle dark gradient at the top edge (y=0) to anchor the frame, peaks softly around center_y,
    and fades completely seamlessly down to 0 at the piano keys.
    Eliminates any localized cloud boundaries and produces a natural, ultra-smooth lighting transition.
    """
    w, h = base_img.size
    backdrop_arr = np.zeros((h, w, 4), dtype=np.uint8)
    
    for y in range(h):
        if y >= keys_y:
            alpha = 0
        elif y <= center_y:
            # Smooth blend from top_alpha at y=0 to peak_alpha at center_y
            t = y / float(center_y) if center_y > 0 else 1.0
            smooth_t = 3 * (t**2) - 2 * (t**3)
            alpha = int(top_alpha + (peak_alpha - top_alpha) * smooth_t)
        else:
            # Smooth ease-out from peak_alpha at center_y to 0 at keys_y
            t = (y - center_y) / float(keys_y - center_y)
            alpha = int(peak_alpha * (math.cos(t * (math.pi / 2.0)) ** 2))
            
        backdrop_arr[y, :, 3] = alpha
        
    backdrop_img = Image.fromarray(backdrop_arr, mode="RGBA")
    backdrop_img = backdrop_img.filter(ImageFilter.GaussianBlur(radius=20))
    return Image.alpha_composite(base_img, backdrop_img)

def draw_text_with_theme_glow(base_img, text, center_x, center_y, font, theme='warm', letter_spacing=14, is_title=True):
    theme_key = theme.lower().strip()
    palette = THEME_GLOW_PALETTES.get(theme_key, THEME_GLOW_PALETTES['warm'])
    
    w, h = base_img.size
    lines = text.split('\n')
    
    text_canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_tc = ImageDraw.Draw(text_canvas)
    
    line_layouts = []
    total_text_h = 0
    line_gap = 25 if is_title else 15
    
    for line in lines:
        if not line:
            continue
        chars = list(line)
        char_widths = [draw_tc.textlength(c, font=font) for c in chars]
        line_w = sum(char_widths) + (len(chars) - 1) * letter_spacing
        bbox = draw_tc.textbbox((0, 0), line, font=font)
        char_h = bbox[3] - bbox[1]
        line_layouts.append({
            'text': line,
            'chars': chars,
            'widths': char_widths,
            'total_w': line_w,
            'h': char_h
        })
        total_text_h += char_h
        
    total_text_h += (len(line_layouts) - 1) * line_gap
    start_y = center_y - (total_text_h / 2.0)
    
    curr_y = start_y
    for l_info in line_layouts:
        curr_x = center_x - (l_info['total_w'] / 2.0)
        for c, cw in zip(l_info['chars'], l_info['widths']):
            draw_tc.text((curr_x, curr_y), c, font=font, fill=(255, 255, 255, 255))
            curr_x += cw + letter_spacing
        curr_y += l_info['h'] + line_gap
        
    shadow_mask = text_canvas.split()[3]
    
    # 1. Tight Dense Black Shadow for Razor-Sharp Glyph Edge Contrast
    dense_shadow = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    dense_shadow.putalpha(shadow_mask)
    dense_blur = dense_shadow.filter(ImageFilter.GaussianBlur(radius=6))
    
    dense_offset = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dense_offset.paste(dense_blur, (0, 5), dense_blur)
    base_img = Image.alpha_composite(base_img, dense_offset)
    
    # 2. Medium Drop Shadow
    med_shadow = Image.new("RGBA", (w, h), (0, 0, 0, 210))
    med_shadow.putalpha(shadow_mask)
    med_blur_s = med_shadow.filter(ImageFilter.GaussianBlur(radius=14))
    shadow_offset_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow_offset_img.paste(med_blur_s, (0, 7), med_blur_s)
    base_img = Image.alpha_composite(base_img, shadow_offset_img)
    
    # 3. Broad Outer Colored Glow
    glow_rgb = palette['glow_rgb']
    broad_glow = Image.new("RGBA", (w, h), (*glow_rgb, 140))
    broad_glow.putalpha(shadow_mask)
    broad_blur = broad_glow.filter(ImageFilter.GaussianBlur(radius=32))
    base_img = Image.alpha_composite(base_img, broad_blur)
    
    # 4. Medium Colored Glow
    inner_rgb = palette['inner_glow']
    med_glow = Image.new("RGBA", (w, h), (*inner_rgb, 210))
    med_glow.putalpha(shadow_mask)
    med_blur = med_glow.filter(ImageFilter.GaussianBlur(radius=12))
    base_img = Image.alpha_composite(base_img, med_blur)
    
    # 5. Tight Core Glow Aura
    tight_glow = Image.new("RGBA", (w, h), (*glow_rgb, 245))
    tight_glow.putalpha(shadow_mask)
    tight_blur = tight_glow.filter(ImageFilter.GaussianBlur(radius=4))
    base_img = Image.alpha_composite(base_img, tight_blur)
    
    # 6. Solid Crisp Pure White Foreground
    base_img = Image.alpha_composite(base_img, text_canvas)
    return base_img, start_y + total_text_h

def generate_widescreen_cover_highlight(
    bg_frame,
    song_title,
    author,
    theme,
    output_path,
    badge_text=None,
    is_tutorial=False
):
    import cv2
    if isinstance(bg_frame, np.ndarray):
        bg_rgb = Image.fromarray(cv2.cvtColor(bg_frame, cv2.COLOR_BGR2RGB))
    else:
        bg_rgb = bg_frame.convert("RGB")
        
    resample_filter = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.ANTIALIAS
    img = bg_rgb.resize((1920, 1080), resample_filter).convert("RGBA")
    
    width, height = img.size
    center_x = width // 2
    
    font_arno = os.path.join(tools_dir, 'fonts', 'arno_pro.ttf')
    font_mont_bold = os.path.join(tools_dir, 'fonts', 'Montserrat-Bold.ttf')
    font_mont = os.path.join(tools_dir, 'fonts', 'montserrat.ttf')
    
    lower_title = song_title.lower()
    if "easy" in lower_title:
        if not badge_text:
            badge_text = "EASY"
        import re
        song_title = re.sub(r'\s*[\(\[-]\s*easy\s*[\)\]-]?\s*', '', song_title, flags=re.IGNORECASE).strip()
        song_title = re.sub(r'\b_?easy_?\b', '', song_title, flags=re.IGNORECASE).strip()
        
    if any(kw in os.path.basename(output_path).lower() for kw in ["slow", "tutorial", "tuto"]):
        is_tutorial = True
        
    title_center_y = int(height * 0.38)
    
    # 1. Apply Full-Width Seamless Continuous Top-Down Vignette
    img = apply_cinematic_contrast_backdrop(img, center_y=title_center_y, keys_y=790, peak_alpha=160, top_alpha=100)
    
    # 2. Title Typography
    formatted_title = song_title.upper()
    title_font_size = 115
    title_font = ImageFont.truetype(font_arno, title_font_size)
    letter_spacing = 14
    
    draw_dummy = ImageDraw.Draw(img)
    char_widths = [draw_dummy.textlength(c, font=title_font) for c in formatted_title]
    total_w = sum(char_widths) + (len(formatted_title) - 1) * letter_spacing
    
    if total_w > 1500:
        title_font_size = int(title_font_size * (1500 / total_w))
        title_font = ImageFont.truetype(font_arno, title_font_size)
        letter_spacing = int(letter_spacing * (1500 / total_w))
        
    img, _ = draw_text_with_theme_glow(
        img, formatted_title, center_x, title_center_y, title_font,
        theme=theme, letter_spacing=letter_spacing, is_title=True
    )
    
    # 3. Badges (EASY / PREVIEW)
    if badge_text:
        draw = ImageDraw.Draw(img)
        badge_font_size = 40
        badge_font = ImageFont.truetype(font_mont_bold, badge_font_size)
        badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
        badge_w = badge_bbox[2] - badge_bbox[0]
        badge_h = badge_bbox[3] - badge_bbox[1]
        pad_x = 32
        pad_y = 16
        pill_w = badge_w + 2 * pad_x
        pill_h = badge_h + 2 * pad_y
        
        pill_x1 = 70
        pill_y1 = 65
        pill_x2 = pill_x1 + pill_w
        pill_y2 = pill_y1 + pill_h
        
        if badge_text.upper() == "EASY":
            pill_fill = (10, 42, 28, 235)
            pill_outline = (52, 211, 153, 245)
        else:
            pill_fill = (36, 16, 56, 235)
            pill_outline = (192, 132, 252, 245)
            
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        o_draw = ImageDraw.Draw(overlay)
        o_draw.rounded_rectangle([pill_x1, pill_y1, pill_x2, pill_y2], radius=int(pill_h // 2), fill=pill_fill, outline=pill_outline, width=3)
        img = Image.alpha_composite(img, overlay)
        
        draw = ImageDraw.Draw(img)
        rect_cx = (pill_x1 + pill_x2) / 2.0
        rect_cy = (pill_y1 + pill_y2) / 2.0
        tx = rect_cx - (badge_bbox[0] + badge_bbox[2]) / 2.0
        ty = rect_cy - (badge_bbox[1] + badge_bbox[3]) / 2.0
        draw.text((tx, ty), badge_text, font=badge_font, fill=(255, 255, 255, 255))
        
    # 5. TUTORIAL ribbon (top-right)
    if is_tutorial:
        ref = height
        dx2 = int(ref * 0.20)
        dx1 = int(ref * 0.48)
        dy2 = int(ref * 0.11)
        dy1 = int(ref * 0.27)
        
        pA = (width - dx1, 0)
        pB = (width - dx2, 0)
        pC = (width, dy2)
        pD = (width, dy1)
        
        ribbon_shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        rs_draw = ImageDraw.Draw(ribbon_shadow)
        rs_draw.polygon([pA, pB, pC, pD], fill=(0, 0, 0, 180))
        ribbon_shadow = ribbon_shadow.filter(ImageFilter.GaussianBlur(radius=8))
        img = Image.alpha_composite(img, ribbon_shadow)
        
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        o_draw = ImageDraw.Draw(overlay)
        o_draw.polygon([pA, pB, pC, pD], fill=(255, 255, 255, 255))
        img = Image.alpha_composite(img, overlay)
        
        dx_mid = (dx1 + dx2) / 2.0
        dy_mid = (dy1 + dy2) / 2.0
        cx = width - dx_mid / 2.0
        cy = dy_mid / 2.0
        angle_deg = math.degrees(math.atan2(dy_mid, dx_mid))
        
        shift_offset = ref * 0.017
        cx = cx + shift_offset * math.cos(math.radians(angle_deg))
        cy = cy + shift_offset * math.sin(math.radians(angle_deg))
        
        tuto_font_size = int(ref * 0.062)
        try:
            tuto_font = ImageFont.truetype("ariblk.ttf", tuto_font_size)
        except Exception:
            tuto_font = ImageFont.truetype(font_mont_bold, tuto_font_size)
            
        text_str = "TUTORIAL"
        text_w = int(ref * 0.6)
        text_h = int(ref * 0.2)
        text_img = Image.new("RGBA", (text_w, text_h), (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_img)
        text_draw.text((text_w / 2.0, text_h / 2.0), text_str, font=tuto_font, fill=(0, 0, 0, 255), anchor="mm")
        
        resample_filter = Image.Resampling.BICUBIC if hasattr(Image, 'Resampling') else Image.BICUBIC
        rotated_text_img = text_img.rotate(-angle_deg, expand=True, resample=resample_filter)
        
        w_rot, h_rot = rotated_text_img.size
        px = int(cx - w_rot / 2.0)
        py = int(cy - h_rot / 2.0)
        img.paste(rotated_text_img, (px, py), rotated_text_img)

    final_rgb = img.convert("RGB")
    final_rgb.save(output_path, quality=96)
    print(f"Saved Widescreen Cover: {output_path}")
    return True

def generate_cover(theme, bg_filename, song_title, author, output_path, is_wide=False, badge_text=None, video_path=None):
    if is_wide:
        highlight_frame = None
        if video_path and os.path.exists(video_path):
            highlight_frame = extract_highlight_frame(video_path)
            
        if highlight_frame is None:
            clean_name = song_title
            import re
            clean_name = re.sub(r'\s*[\(\[-]\s*(?:easy|slow|teaser|tutorial)\s*[\)\]-]?\s*', '', clean_name, flags=re.IGNORECASE).strip()
            
            candidates = [
                os.path.join(r"C:\Dev\meloscribe\TikToks", f"{clean_name}_wide.mp4"),
                os.path.join(r"C:\Dev\meloscribe\Keysight export", f"{clean_name}.mp4"),
                os.path.join(r"C:\Dev\meloscribe\TikToks", f"{clean_name}.mp4")
            ]
            for cand in candidates:
                if os.path.exists(cand):
                    highlight_frame = extract_highlight_frame(cand)
                    if highlight_frame is not None:
                        break
                        
        if highlight_frame is not None:
            return generate_widescreen_cover_highlight(
                highlight_frame, song_title, author, theme, output_path, badge_text=badge_text
            )
            
    # Fallback / Vertical cover generation
    lower_title = song_title.lower()
    if "easy" in lower_title:
        if not badge_text:
            badge_text = "EASY"
        import re
        song_title = re.sub(r'\s*[\(\[-]\s*easy\s*[\)\]-]?\s*', '', song_title, flags=re.IGNORECASE).strip()
        song_title = re.sub(r'\b_?easy_?\b', '', song_title, flags=re.IGNORECASE).strip()

    bg_path = os.path.join(tools_dir, "themes", theme, bg_filename)
    if not os.path.exists(bg_path):
        fallback_filename = "1_wide.jpg" if is_wide else "1.png"
        bg_path = os.path.join(tools_dir, "themes", theme, fallback_filename)
        
    if not os.path.exists(bg_path):
        print(f"Error: Background template not found: {bg_path}")
        return False
        
    spartan_path, montserrat_path = ensure_fonts()
    
    try:
        img_orig = Image.open(bg_path).convert("RGBA")
        img = img_orig
        draw = ImageDraw.Draw(img)
        width, height = img.size
        center_x = width // 2
        max_text_width = int(width * 0.8)
        
        if is_wide:
            title_size = int(width * 0.08)
            author_size = int(width * 0.045)
        else:
            title_size = int(width * 0.15)
            author_size = int(width * 0.055)
        
        while title_size > 20:
            title_font = ImageFont.truetype(spartan_path, title_size)
            avg_char_width = max(1, int(title_size * 0.55))
            wrap_chars = max(5, int(max_text_width / avg_char_width))
            wrapper = textwrap.TextWrapper(width=wrap_chars, break_long_words=False)
            wrapped_title = wrapper.fill(text=song_title)
            lines = wrapped_title.split('\n')
            max_line_width = max([title_font.getlength(line) for line in lines])
            if max_line_width <= max_text_width:
                break
            title_size -= 5
            
        if max_line_width > max_text_width:
            wrapper = textwrap.TextWrapper(width=wrap_chars, break_long_words=True)
            wrapped_title = wrapper.fill(text=song_title)
            
        num_lines = wrapped_title.count('\n') + 1
        if num_lines > 3:
            title_size = int(title_size * 0.8)
            title_font = ImageFont.truetype(spartan_path, title_size)
            
        author_font = ImageFont.truetype(montserrat_path, author_size)
        base_start_y = int(height * 0.28) if not is_wide else int(height * 0.30)
        
        orig_title_font = ImageFont.truetype(spartan_path, int(width * 0.15) if not is_wide else int(width * 0.08))
        bbox1 = draw.multiline_textbbox((center_x, base_start_y), "Dummy", font=orig_title_font, align="center", anchor="ma", spacing=60)
        center_1_line = (bbox1[1] + bbox1[3]) / 2.0
        
        bbox2_test = draw.multiline_textbbox((center_x, 0), "A\nB", font=orig_title_font, align="center", anchor="ma", spacing=60)
        h2 = bbox2_test[3] - bbox2_test[1]
        target_bottom_2_lines = center_1_line + (h2 / 2.0)
        
        actual_bbox = draw.multiline_textbbox((center_x, 0), wrapped_title, font=title_font, align="center", anchor="ma", spacing=60)
        actual_height = actual_bbox[3] - actual_bbox[1]
        
        if num_lines == 1:
            start_y = base_start_y
        elif num_lines == 2:
            start_y = int(center_1_line - (actual_height / 2.0) - actual_bbox[1])
        else:
            shift_down = 100 if not is_wide else 40
            start_y = int(target_bottom_2_lines - actual_height - actual_bbox[1]) + shift_down
        
        draw_text_with_shadow(draw, wrapped_title, center_x, start_y, title_font, shadow_offset=(6, 6), anchor="ma", spacing=60)
        
        if badge_text:
            badge_font_size = int(width * 0.035) if is_wide else int(width * 0.055)
            badge_font = ImageFont.truetype(montserrat_path, badge_font_size)
            badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
            badge_w = badge_bbox[2] - badge_bbox[0]
            badge_h = badge_bbox[3] - badge_bbox[1]
            pad_x = int(badge_font_size * 0.8)
            pad_y = int(badge_font_size * 0.4)
            pill_w = badge_w + 2 * pad_x
            pill_h = badge_h + 2 * pad_y
            
            pill_y1 = int(height * 0.06) if not is_wide else int(height * 0.08)
            pill_x1 = int(width * 0.06) if not is_wide else int(width * 0.08)
            pill_x2 = pill_x1 + pill_w
            pill_y2 = pill_y1 + pill_h
            
            if badge_text.upper() == "EASY":
                pill_fill = (12, 38, 28, 225)
                pill_outline = (52, 211, 153, 240)
            else:
                pill_fill = (32, 18, 54, 225)
                pill_outline = (192, 132, 252, 240)

            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rounded_rectangle([pill_x1, pill_y1, pill_x2, pill_y2], radius=int(pill_h // 2), fill=pill_fill, outline=pill_outline, width=3)
            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)
            rect_center_x = (pill_x1 + pill_x2) / 2.0
            rect_center_y = (pill_y1 + pill_y2) / 2.0
            text_x = rect_center_x - (badge_bbox[0] + badge_bbox[2]) / 2.0
            text_y = rect_center_y - (badge_bbox[1] + badge_bbox[3]) / 2.0
            draw.text((text_x, text_y), badge_text, font=badge_font, fill=(255, 255, 255, 255))

        is_tutorial = any(kw in os.path.basename(output_path).lower() for kw in ["slow", "tutorial", "tuto"])
        if is_tutorial:
            ref = height if is_wide else width
            dx2 = int(ref * 0.20)
            dx1 = int(ref * 0.48)
            dy2 = int(ref * 0.11)
            dy1 = int(ref * 0.27)
            
            pA = (width - dx1, 0)
            pB = (width - dx2, 0)
            pC = (width, dy2)
            pD = (width, dy1)
            
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.polygon([pA, pB, pC, pD], fill=(255, 255, 255, 255))
            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)
            
            dx_mid = (dx1 + dx2) / 2.0
            dy_mid = (dy1 + dy2) / 2.0
            cx = width - dx_mid / 2.0
            cy = dy_mid / 2.0
            angle_deg = math.degrees(math.atan2(dy_mid, dx_mid))
            
            shift_offset = ref * 0.017
            cx = cx + shift_offset * math.cos(math.radians(angle_deg))
            cy = cy + shift_offset * math.sin(math.radians(angle_deg))
            
            tuto_font_size = int(ref * 0.062)
            try:
                tuto_font = ImageFont.truetype("ariblk.ttf", tuto_font_size)
            except Exception:
                tuto_font = ImageFont.truetype(spartan_path, tuto_font_size)
            
            text_str = "TUTORIAL"
            text_w = int(ref * 0.6)
            text_h = int(ref * 0.2)
            text_img = Image.new("RGBA", (text_w, text_h), (0, 0, 0, 0))
            text_draw = ImageDraw.Draw(text_img)
            text_draw.text((text_w / 2.0, text_h / 2.0), text_str, font=tuto_font, fill=(0, 0, 0, 255), anchor="mm")
            
            resample_filter = Image.Resampling.BICUBIC if hasattr(Image, 'Resampling') else Image.BICUBIC
            rotated_text_img = text_img.rotate(-angle_deg, expand=True, resample=resample_filter)
            
            w_rot, h_rot = rotated_text_img.size
            px = int(cx - w_rot / 2.0)
            py = int(cy - h_rot / 2.0)
            img.paste(rotated_text_img, (px, py), rotated_text_img)
            draw = ImageDraw.Draw(img)
        
        bbox = draw.multiline_textbbox((center_x, start_y), wrapped_title, font=title_font, align="center", anchor="ma", spacing=60)
        title_bottom_y = bbox[3]
        
        padding = 80 if not is_wide else (80 if num_lines == 1 else (75 if num_lines == 2 else 60))
        author_y = title_bottom_y + padding
        draw_text_with_shadow(draw, author, center_x, author_y, author_font, shadow_offset=(4, 4), anchor="ma", spacing=60)
        
        img = img.convert("RGB")
        if is_wide:
            try:
                resample_filter = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.ANTIALIAS
                img = img.resize((1920, 1080), resample_filter)
            except Exception as e:
                print(f"Resize failed: {e}")
        img.save(output_path, quality=95)
        print(f"Saved Cover: {output_path}")
        return True
    
    except Exception as e:
        print(f"Draw failed: {e}")
        return False

def generate_clean_cover(theme, bg_filename, output_path):
    bg_path = os.path.join(tools_dir, "themes", theme, bg_filename)
    if not os.path.exists(bg_path):
        fallback_filename = "1.png"
        bg_path = os.path.join(tools_dir, "themes", theme, fallback_filename)
    if not os.path.exists(bg_path):
        print(f"Error: Background template not found for clean cover: {bg_path}")
        return False
    try:
        img = Image.open(bg_path).convert("RGB")
        img.save(output_path, quality=95)
        print(f"Saved Clean Cover: {output_path}")
        return True
    except Exception as e:
        print(f"Clean cover failed: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--song", required=True)
    parser.add_argument("--author", required=True)
    parser.add_argument("--theme", required=True)
    parser.add_argument("--wide", action="store_true", help="Generate only widescreen covers")
    parser.add_argument("--vertical", action="store_true", help="Generate only vertical covers")
    parser.add_argument("--video", default=None, help="Optional direct video path to extract highlight from")
    
    args = parser.parse_args()
    
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), "meloscribe", "backend"))
    try:
        from settings import load_settings
        settings = load_settings()
        export_dir = settings.get("covers_dir", r"C:\Dev\meloscribe\Covers")
    except Exception:
        export_dir = r"C:\Dev\meloscribe\Covers"
        
    os.makedirs(export_dir, exist_ok=True)
    
    base_song = args.song
    if base_song.lower().endswith(" easy"):
        base_song = base_song[:-5].strip()
    elif base_song.lower().endswith(" teaser"):
        base_song = base_song[:-7].strip()
    
    import shutil
    
    do_wide = args.wide or (not args.vertical)
    do_vertical = args.vertical or (not args.wide)
    
    if do_wide:
        out_norm = os.path.join(export_dir, f"{base_song}_wide.jpg")
        out_tuto = os.path.join(export_dir, f"{base_song} slow_wide.jpg")
        out_easy = os.path.join(export_dir, f"{base_song} easy_wide.jpg")
        out_easy_tuto = os.path.join(export_dir, f"{base_song} easy slow_wide.jpg")
        out_hook = os.path.join(export_dir, f"{base_song} hook_wide.jpg")
        
        print(f"Generating widescreen highlight covers for theme: '{args.theme}'")
        generate_cover(args.theme, "1_wide.jpg", base_song, f"{args.author}", out_norm, is_wide=True, video_path=args.video)
        generate_cover(args.theme, "1_wide.jpg", base_song, f"{args.author}", out_tuto, is_wide=True, video_path=args.video)
        generate_cover(args.theme, "1_wide.jpg", base_song, f"{args.author}", out_easy, is_wide=True, badge_text="EASY", video_path=args.video)
        generate_cover(args.theme, "1_wide.jpg", base_song, f"{args.author}", out_easy_tuto, is_wide=True, badge_text="EASY", video_path=args.video)
        generate_cover(args.theme, "1_wide.jpg", base_song, f"{args.author}", out_hook, is_wide=True, badge_text="BEST PART", video_path=args.video)
        
        if args.song.lower() != base_song.lower():
            alias_easy_tuto = os.path.join(export_dir, f"{args.song} slow_wide.jpg")
            if alias_easy_tuto.lower() != out_easy_tuto.lower():
                shutil.copy2(out_easy_tuto, alias_easy_tuto)
                
    if do_vertical:
        out_norm = os.path.join(export_dir, f"{base_song}.jpg")
        out_tuto = os.path.join(export_dir, f"{base_song} slow.jpg")
        out_easy = os.path.join(export_dir, f"{base_song} easy.jpg")
        out_easy_tuto = os.path.join(export_dir, f"{base_song} easy slow.jpg")
        out_hook = os.path.join(export_dir, f"{base_song} hook.jpg")
        out_clean = os.path.join(export_dir, f"{base_song}_clean.jpg")
        
        print(f"Generating vertical covers for theme: '{args.theme}'")
        generate_cover(args.theme, "1.png", base_song, f"- {args.author} -", out_norm, is_wide=False)
        generate_cover(args.theme, "1.png", base_song, f"- {args.author} -", out_tuto, is_wide=False)
        generate_cover(args.theme, "1.png", base_song, f"- {args.author} -", out_easy, is_wide=False, badge_text="EASY")
        generate_cover(args.theme, "1.png", base_song, f"- {args.author} -", out_easy_tuto, is_wide=False, badge_text="EASY")
        generate_cover(args.theme, "1.png", base_song, f"- {args.author} -", out_hook, is_wide=False, badge_text="BEST PART")
        generate_clean_cover(args.theme, "1.png", out_clean)
        
        if args.song.lower() != base_song.lower():
            alias_easy_tuto = os.path.join(export_dir, f"{args.song} slow.jpg")
            if alias_easy_tuto.lower() != out_easy_tuto.lower():
                shutil.copy2(out_easy_tuto, alias_easy_tuto)
        
    print("Cover Generation Complete.")
