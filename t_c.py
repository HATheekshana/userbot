import asyncio
import aiohttp
import json
from io import BytesIO
from PIL import Image, ImageDraw, ImageOps, ImageFilter, ImageFont

# --- DATA EXTRACTION ---
def get_user_char_data(avatar_list, char_id, avatars_db):
    for char in avatar_list:
        if str(char.get("avatarId")) == str(char_id):
            meta = avatars_db.get(str(char_id), {})
            skill_levels = []
            order = meta.get("SkillOrder", [])
            p_map = meta.get("ProudMap", {})
            base_s = char.get("skillLevelMap", {})
            extra_s = char.get("proudSkillExtraLevelMap", {})
            
            for sid in order:
                lvl = base_s.get(str(sid), 1) + extra_s.get(str(p_map.get(str(sid))), 0)
                skill_levels.append(lvl)

            return {
                "talents": skill_levels,
                "cons_count": len(char.get("talentIdList", [])),
                "cons_icons": meta.get("Consts", []),
                "skill_icons": [meta["Skills"][str(s)] for s in meta["SkillOrder"]]
            }
    return None
def draw_circle_bubble(draw, text, position, font, padding=10, text_color=(255, 255, 255, 255), anchor="mm"):
    # 1. Get the text size
    bbox = draw.textbbox(position, text, font=font, anchor=anchor)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    # 2. Find the largest dimension to make it a square
    # We add padding to the diameter
    diameter = max(w, h) + (padding * 2)
    
    # 3. Calculate the bounding box for the circle centered at 'position'
    # position[0] is x, position[1] is y
    left = position[0] - (diameter // 2)
    top = position[1] - (diameter // 2)
    right = position[0] + (diameter // 2)
    bottom = position[1] + (diameter // 2)
    
    # 4. Draw the Circle (Ellipse in a square box)
    draw.ellipse([left, top, right, bottom], fill=(20, 20, 30, 200), outline=(255, 255, 255, 150), width=1)
    
    # 5. Draw the text
    draw.text(position, text, font=font, fill=text_color, anchor=anchor)
async def fetch_ui_image(session, url):
    try:
        async with session.get(f"https://enka.network/ui/{url.replace('/ui/','')}", timeout=10) as r:
            if r.status == 200:
                return Image.open(BytesIO(await r.read())).convert("RGBA")
    except: pass
    return None

# --- DATA FETCHING (Call this from your main card code) ---
async def fetch_build_assets(uid,char_id):
    with open('avatars.json', 'r') as f: 
        avatars_db = json.load(f)

    async with aiohttp.ClientSession() as session:
        r1 = await session.get(f"https://enka.network/api/uid/{uid}")
        d1 = await r1.json()

        me_data = get_user_char_data(d1.get("avatarInfoList", []), char_id, avatars_db)

        if not me_data:
            return None, None

        # Fetch icons for both (showing me_data icons as the reference)
        t_icons = await asyncio.gather(*[fetch_ui_image(session, u) for u in me_data['skill_icons']])
        c_icons = await asyncio.gather(*[fetch_ui_image(session, u) for u in me_data['cons_icons']])
        
    return me_data, t_icons, c_icons

# --- DRAWING TOOL (Call this from your main card code) ---
def draw_build_column(canvas, start_x, data, t_icons, c_icons):
    draw = ImageDraw.Draw(canvas)
    font_path = "asstests/fonts/Genshin_Impact.ttf"
    f_lvl = ImageFont.truetype(font_path, 18)
    
    # Load assets
    entry_bg = Image.open("asstests/talents/bg.png").convert("RGBA")
    ten_bg = Image.open("asstests/talents/10.png").convert("RGBA")
    con_bg = Image.open("asstests/constant/const_adapt.png").convert("RGBA")
    lock_bg = Image.open("asstests/constant/closed/CLOSED.png").convert("RGBA")
    mask = Image.open("asstests/constant/maska_constant.png").convert("L")

    # --- DRAW TALENTS (Straight Line) ---
    talent_x = start_x + 30
    talent_y_base = 330
    
    for i, icon in enumerate(t_icons):
        if not icon: continue
        y = talent_y_base + (i * 105)
        lvl = data['talents'][i]
        
        # 1. Black Background Circle
        draw.ellipse([talent_x + 10, y + 10, talent_x + 80, y + 80], fill=(0, 0, 0, 180))
        
        # 2. Talent Frame
        t_bg = (ten_bg if lvl >= 10 else entry_bg).resize((90, 90), Image.Resampling.LANCZOS)
        canvas.paste(t_bg, (talent_x, y), t_bg)

        # 3. Icon
        icon_res = icon.resize((60, 60), Image.Resampling.LANCZOS)
        canvas.paste(icon_res, (talent_x + 15, y + 15), icon_res)

        # 4. Level Bubble
        color = (255, 215, 0) if lvl >= 10 else (255, 255, 255)
        draw_circle_bubble(draw, f"{lvl}", (talent_x + 45, y + 85), f_lvl, text_color=color)

    # --- DRAW CONSTELLATIONS (Straight Line) ---
    const_x = start_x - 600 
    const_y_base = 250
    
    for i, icon in enumerate(c_icons):
        if not icon: continue
        y = const_y_base + (i * 95)
        is_locked = i >= data['cons_count']
        
        # 1. Black Background Circle
        draw.ellipse([const_x + 5, y + 5, const_x + 65, y + 65], fill=(0, 0, 0, 180))

        c_mask = mask.resize((60, 60), Image.Resampling.LANCZOS) 
        img = icon.resize((60, 60), Image.Resampling.LANCZOS)

        if is_locked:
            # LOCKED: Draw grayscale icon behind, then lock frame ON TOP
            img = img.convert("L").convert("RGBA")
            lock_frame = lock_bg.resize((70, 70), Image.Resampling.LANCZOS)
            
            canvas.paste(img, (const_x + 5, y + 5), c_mask)
            canvas.paste(lock_frame, (const_x, y), lock_frame)
        else:
            # UNLOCKED: Draw glowing frame behind, then colored icon ON TOP
            con_frame = con_bg.resize((70, 70), Image.Resampling.LANCZOS)
            
            canvas.paste(con_frame, (const_x, y), con_frame)
            canvas.paste(img, (const_x + 5, y + 5), c_mask)