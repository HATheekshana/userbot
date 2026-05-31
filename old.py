async def compare_characters(uid, char_id):
    
    try:
        me= await get_enkadata(uid)
        me_data,t_icons, c_icons = await fetch_build_assets(uid,char_id)

    except Exception as e:
        print("--- CRITICAL ERROR IN IMAGE GENERATION ---")
        print("------------------------------------------")
        return None
    char_id_str = str(char_id)
    char_info = CHAR_MAP.get(char_id_str, {"element": "Anemo", "avataricon": "UI_AvatarIcon_Qin"})
    
    initial_element = char_info.get('element', 'Anemo')
    stats = extract_char_stats(me['avatarInfoList'], char_id, initial_element)
    
    element = stats.get("element", initial_element) if stats else initial_element
    
    avatar_icon = char_info.get("avataricon", "UI_AvatarIcon_Zibai")
    char_level = stats.get("char_level", 1) if stats else 1
    f_level = stats.get("friendship", 1) if stats else 1
    char_name = avatar_icon.replace("UI_AvatarIcon_", "")
    char_info = next(c for c in me['avatarInfoList'] if str(c.get("avatarId")) == str(char_id))
    target_size = (1875, 890)
    headers = {"User-Agent": "Mozilla/5.0"}
    try: 
        font = ImageFont.truetype("Genshin_Impact.ttf", 23)
        font_big = ImageFont.truetype("Genshin_Impact.ttf", 28)
        font_small = ImageFont.truetype("Genshin_Impact.ttf", 20)
        font_xsmall = ImageFont.truetype("Genshin_Impact.ttf", 16)
        
    except: 
        font = ImageFont.load_default()
    async with aiohttp.ClientSession(headers=headers) as session:
        splash_task = asyncio.create_task(fetch_image(session, get_splash_url(avatar_icon)))
        bg_urls = get_namecard_urls(avatar_icon)
        
        bg_img = None
        for url in bg_urls:
            bg_img = await fetch_image(session, url)
            if bg_img: break
        
        splash_img = await splash_task
        weapon_ic = stats['weapon'].get('icon')
        weapon_url = f"https://enka.network/ui/{weapon_ic}.png" if weapon_ic else "https://enka.network/ui/UI_EquipIcon_Sword_Blunt.png"
        fetched_stuff = await asyncio.gather(fetch_image(session, weapon_url))
        weapon_img = fetched_stuff[0]
    if not bg_img:
        bg_img = Image.new("RGBA", target_size, (30, 30, 45, 255)) # Emergency fallback
    bg = ImageOps.fit(bg_img, target_size, method=Image.Resampling.LANCZOS).convert("RGBA")
    bg = ImageEnhance.Brightness(bg).enhance(0.45)
    ui_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(ui_layer)
    if splash_img:
        ui_layer = paste_splash_left(ui_layer, splash_img, target_size)

    font_path = "asstests/fonts/Genshin_Impact.ttf"

    draw_text_with_shadow(draw,text=char_name,position=(50, 50),font_path=font_path,font_size=36,text_color=(255, 255, 255, 255), anchor="lm")
    draw_text_with_shadow(draw,text=me['nickname'],position=(150, 50),font_path=font_path,font_size=26,text_color=(255, 255, 255, 255), anchor="lm")
    draw_text_with_shadow(draw,text=f"Lvl: {char_level}/90",position=(50, 90),font_path=font_path,font_size=24,text_color=(255, 255, 255, 255), anchor="lm")
    draw_text_with_shadow(draw,text=f"Friendship: {f_level}",position=(50, 125),font_path=font_path,font_size=24,text_color=(255, 255, 255, 255), anchor="lm")

#weapon
# Assuming your single user's variables are named weapon_img and stats
    if weapon_img and stats:
        pos = (1000, 50) 
        w_size = (120, 120)
        wp_res = ImageOps.contain(weapon_img, w_size, Image.Resampling.LANCZOS)
        off_x = (w_size[0] - wp_res.width) // 2
        off_y = (w_size[1] - wp_res.height) // 2
        final_pos = (pos[0] + off_x, pos[1] + off_y)
        ui_layer.paste(wp_res, final_pos, wp_res)
        
        w_info = stats['weapon']
        weapon_name = get_weapon_name(w_info)
        draw_text_with_shadow(draw, weapon_name, (pos[0] + 150, pos[1] + 30), font_path, 28, text_color=(255, 255, 255), anchor="lm") 
        refine = w_info.get('refinement', 1)
        level = w_info.get('level', 1)
        max_lv = "90" if w_info.get('rank', 0) == 5 else "80" if w_info.get('rank', 0) == 4 else "70"
        
        lv_text = f"R{refine}      Lv.{level}/{max_lv}"
        draw_text_with_shadow(draw, lv_text, (pos[0] + 150, pos[1] + 70), font_path, 24, text_color=(255, 255, 255), anchor="lm")

        # 4. Draw Base ATK and Sub Stat Boxes
        w_stats_list = w_info.get("stats", [])
        stat_x_start = pos[0] + 150
        stat_y = pos[1] + 90
        for i, s in enumerate(w_stats_list):
            curr_stat_x = stat_x_start + (i * 125) 
            
            # Draw Box
            draw.rounded_rectangle([curr_stat_x, stat_y, curr_stat_x + 115, stat_y + 40], radius=5, fill=(15, 15, 25, 200))
            
            # Draw Stat Icon
            icon_path = W_STAT_ICONS.get(s['prop'], "asstests/icons/atk.png")
            try:
                s_icon = Image.open(icon_path).convert("RGBA").resize((22, 22))
                ui_layer.paste(s_icon, (curr_stat_x + 5, stat_y + 10), s_icon)
            except:
                pass
        
            # Draw Value
            val_str = f"{s['val']}"
            # Simplified percentage check
            if any(x in str(s['prop']) for x in ["PERCENT", "CHARGE", "CRITICAL"]):
                val_str += "%"
            
            draw.text((curr_stat_x + 40, stat_y + 20), val_str, font=font_small, fill=(255, 255, 255), anchor="lm")


#stats

    y_start = 220
    icon_w = 60       
    label_w = 600     
    val_w = 170       
    gap = 15          # Increased gap for better breathing room
    row_height = 55
    row_spacing = 50
    start_x = 950      # Moved in from the edge slightly

    # Same config as before
    stat_config = [
        ("Max HP", "hp", "{:.0f}", "asstests/icons/hp.png"),
        ("ATK", "atk", "{:.0f}", "asstests/icons/atk.png"),
        ("DEF", "def", "{:.0f}", "asstests/icons/def.png"),
        ("CRIT Rate", "cr", "{:.1f}%", "asstests/icons/cr.png"),
        ("CRIT DMG", "cd", "{:.1f}%", "asstests/icons/cd.png"),
        ("Energy Recharge", "er", "{:.1f}%", "asstests/icons/er.png"),
        (f"{element} DMG Bonus", "elem_bonus", "{:.1f}%", f"asstests/icons/{element.lower()}.png"),
        ("Elemental Mastery", "em", "{:.0f}", "asstests/icons/em.png")
    ]

    for i, (label, key, fmt, icon_path) in enumerate(stat_config):
        curr_y = y_start + (i * row_spacing)
        
        # 1. DRAW ICON (With small dark circle background)
        try:
            icon = Image.open(icon_path).convert("RGBA").resize((35, 35))
            # Paste centered in the circle
            bg.paste(icon, (start_x + 10, curr_y + 10), icon)
        except: pass

        # 2. DRAW STAT LABEL (Shadow Text)
        text_x = start_x + row_height + gap
        # Using the shadow function logic we discussed
        draw_text_with_shadow(draw, label, (text_x, curr_y + (row_height//2)), 
                              font_path, 24, text_color=(230, 230, 230), anchor="lm")

        # 3. DRAW VALUE (Right Aligned)
        # We align this to the end of your label_w area
        val_x_end = text_x + label_w
        val_text = fmt.format(stats.get(key, 0)) if stats else "0"
        
        draw_text_with_shadow(draw, val_text, (val_x_end, curr_y + (row_height//2)), 
                              font_path, 26, text_color=(255, 255, 255), anchor="rm")
    final_img = Image.alpha_composite(bg, ui_layer)
    await draw_horizontal_artifacts(session, final_img, char_info, 50, 750, font_small)
    draw_build_column(final_img, 580, me_data, t_icons, c_icons) 
    draw = ImageDraw.Draw(final_img)
    save_path = f"{uid}_{char_id}.jpg"
    final_img.convert("RGB").save(save_path, "JPEG", quality=95)
    return save_path