import matplotlib.pyplot as plt
import numpy as np
import io
import json
from PIL import Image

ELEMENT_COLORS = {
    "Pyro": "#FF9999",
    "Electro": "#E0B0FF",
    "Hydro": "#80C0FF",
    "Dendro": "#A5C531",
    "Anemo": "#72E2C2",
    "Geo": "#FFE070",
    "Cryo": "#A0E9FF",
    "Physical": "#FFFFFF"
}

# Load targets once
with open("targets.json", "r", encoding="utf-8") as f:
    TARGETS = json.load(f)

# Exact labels from your reference, matching the angles (clockwise from 12 o'clock)
LABELS = ['HP', 'ATK', 'DEF', 'EM', 'Crit DMG', 'Crit Rate', 'ER', 'Elem DMG']

def generate_full_radar_chart(values, color="#bb86fc", element="Physical"):
    """
    Generates a complete radar chart (spider net, data, and labels) as a PIL image.
    """
    num_vars = len(LABELS)
    
    # Start at the top (pi/2) and rotate clockwise
    angles = np.linspace(np.pi/2, np.pi/2 - 2*np.pi, num_vars, endpoint=False).tolist()
    
    # Close the loop for both values and angles
    plot_values = values + [values[0]]
    plot_angles = angles + [angles[0]]

    # High-resolution figure size
    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
    plt.subplots_adjust(left=0.1, right=0.9, bottom=0.1, top=0.9)
    
    # Transparent background for the plot area
    ax.set_facecolor('none')
    fig.patch.set_alpha(0.0)
    
    # --- DRAWING THE SPIDER NET (取代 png) ---
    # Draw the main outer spine (the regular octagon)
    ax.spines['polar'].set_color('white')
    ax.spines['polar'].set_alpha(0.3)
    ax.spines['polar'].set_linewidth(1.5)
    
    # Draw 5 Concentric "Web" Circles (20%, 40%, 60%, 80%, 100% radial marks)
    ax.set_ylim(0, 1) # Relative percentages (0.0 to 1.0)
    ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels([]) # We don't want to show the numbers
    ax.grid(True, color='white', alpha=0.2, linestyle='-')

    # --- DRAWING THE DATA ---
    # Thick bold line (linewidth=5.0) matching the reference quality
    ax.plot(plot_angles, plot_values, color=color, linewidth=5.0, solid_capstyle='round')
    # Use semi-transparent fill so the web is visible behind it
    ax.fill(plot_angles, plot_values, color=color, alpha=0.45)

    # --- ADDING THE LABELS ---
    # Update the generic label with the specific character element
    display_labels = [l if l != 'Elem DMG' else f"{element} DMG" for l in LABELS]
    
    # Place each label at its correct angle, slightly further out than the web edge
    for angle, label in zip(angles, display_labels):
        # Logic to choose center/left/right horizontal alignment (ha)
        # to prevent text overlap with the web spine
        ha = 'center'
        if 0.1 < angle < 3.0: ha = 'left'   # Top-Right to Bottom-Right
        elif 3.2 < angle < 6.0: ha = 'right' # Bottom-Left to Top-Left
        
        ax.text(angle, 1.15, label, size=16, color='white', 
                weight='bold', ha=ha, va='center', alpha=1)

    # Hide standard degree ticks/labels
    ax.set_xticklabels([])

    # Render to a high-DPI buffer to ensure sharpness
    buf = io.BytesIO()
    plt.savefig(buf, format='png', transparent=True, dpi=300)
    plt.close(fig)
    buf.seek(0)
    
    return Image.open(buf).convert("RGBA")

def get_complete_radar_module(char_stats, char_id, final_size=(450, 450)):
    """
    Looks up targets from targets.json and calls the dynamic chart generator.
    """
    cid_str = str(char_id)
    if cid_str not in TARGETS:
        # Falls back to no-data image logic if character missing from DB
        return None

    targets = TARGETS[cid_str]
    
    # Automatic dynamic settings based on element
    element = char_stats.get("element", "Physical") 
    char_color = ELEMENT_COLORS.get(element, "#FFFFFF")

    # Map the relative values against your manual targets database
    values_list = [
        min(char_stats.get('hp', 0) / targets['hp'], 1.0),
        min(char_stats.get('atk', 0) / targets['atk'], 1.0),
        min(char_stats.get('def', 0) / targets['def'], 1.0),
        min(char_stats.get('em', 0) / targets['em'], 1.0),
        min(char_stats.get('cd', 0) / targets['cd'], 1.0),
        min(char_stats.get('cr', 0) / targets['cr'], 1.0),
        min(char_stats.get('er', 0) / targets['er'], 1.0), # Duplicate Crit DMG point on image
        min(char_stats.get('elem_bonus', 0) / targets.get('dmg_val', 46.6), 1.0), # Generic DMG
    ]

    # Generate the complete image (Web + Data + Text) in one step
    radar_img = generate_full_radar_chart(values_list, char_color, element)
    
    # Scale to fit your card layout
    return radar_img.resize(final_size, Image.Resampling.LANCZOS)