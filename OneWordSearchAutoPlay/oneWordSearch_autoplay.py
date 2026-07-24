from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract
import mss
import tkinter as tk
import time
import numpy as np
import os
import re
import pyautogui
from datetime import datetime
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from wordfreq import zipf_frequency


GRID_SIZE = 5
AUTO_CAPTURE_SECONDS = 3

CAPTURE_REGION = {
    "left": 200,
    "top": 150,
    "width": 500,
    "height": 500
}

# Capture guide sizing
# The guide is kept square. Change GUIDE_INITIAL_SIZE if you always want
# the guide to start bigger/smaller, or use the +/- buttons while aligning.
GUIDE_INITIAL_SIZE = CAPTURE_REGION["width"]
GUIDE_SIZE_STEP = 25
GUIDE_MIN_SIZE = 250
GUIDE_MAX_SIZE = 1000
GUIDE_MOVE_STEP = 1
GUIDE_FAST_MOVE_STEP = 10

# OCR settings
FAST_THRESHOLDS = ["otsu", 190]
FALLBACK_THRESHOLDS = ["otsu", 160, 210]

PSM_MODES_FAST = [10]
PSM_MODES_FALLBACK = [10, 13]

ASK_FOR_UNKNOWN_CELLS = False
SAVE_FAILED_CELLS = False

# Template-saving settings
SAVE_TEMPLATE_IMAGES = True
TEMPLATE_IMAGE_ROOT = "ocr_letter_templates_oneWordSearch"

USE_BACKUP_IMAGE_MATCHING = True
USE_TEMPLATE_MATCHING_FIRST = True
BACKUP_IMAGE_ROOT = os.path.join(TEMPLATE_IMAGE_ROOT, "high_confidence")
BACKUP_MATCH_WHEN_CONF_BELOW = 65
BACKUP_MATCH_THRESHOLD = 0.72
TEMPLATE_FIRST_MATCH_THRESHOLD = 0.78
BACKUP_MATCH_MARGIN = 0.035
BACKUP_MAX_TEMPLATES_PER_LETTER = 80
BACKUP_MATCH_IMAGE_SIZE = 96

HIGH_CONFIDENCE_THRESHOLD = 80
LOW_CONFIDENCE_THRESHOLD = 80

SAVE_CLEANED_TEMPLATE_IMAGE = True
SAVE_ORIGINAL_TEMPLATE_IMAGE = False

# Auto-click/trace settings
AUTO_TRACE_FOUND_WORD = True
TRACE_ONLY_FIRST_WORD = True

TRACE_MOVE_DURATION = 0.12
TRACE_DRAG_DURATION = 0.18
TRACE_PAUSE_AFTER_WORD = 0.5

# Move mouse to top-left corner to abort pyautogui actions
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.03

# Allow 0 because Tesseract often reads round O as zero.
# We map 0 back to O later.
OCR_CONFIG_TEMPLATE = (
    "--psm {psm} "
    "--oem 3 "
    "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0 "
    "-c load_system_dawg=0 "
    "-c load_freq_dawg=0 "
    "-c classify_bln_numeric_mode=0"
)


def is_english_word(word):
    return len(word) == 5 and zipf_frequency(word.lower(), "en") >= 2.5


def show_capture_guide(region):
    root = tk.Tk()
    root.title("Capture Guide")

    # Keep the guide square. This lets you "zoom" the guide larger/smaller
    # while preserving equal width and height.
    start_size = int(region.get("width", GUIDE_INITIAL_SIZE))
    start_size = max(GUIDE_MIN_SIZE, min(GUIDE_MAX_SIZE, start_size))

    guide = {
        "size": start_size,
        "left": int(region.get("left", CAPTURE_REGION["left"])),
        "top": int(region.get("top", CAPTURE_REGION["top"])),
    }

    root.geometry(f"{guide['size']}x{guide['size']}+{guide['left']}+{guide['top']}")
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.35)
    root.overrideredirect(True)

    final_region = region.copy()
    final_region["width"] = guide["size"]
    final_region["height"] = guide["size"]

    cancelled = {"value": False}
    drag_data = {"x": 0, "y": 0}

    canvas = tk.Canvas(
        root,
        width=guide["size"],
        height=guide["size"],
        highlightthickness=0,
        bg="white"
    )
    canvas.pack(fill="both", expand=True)

    button_frame = tk.Frame(root, bg="white")

    def sync_final_region():
        final_region["left"] = root.winfo_x()
        final_region["top"] = root.winfo_y()
        final_region["width"] = guide["size"]
        final_region["height"] = guide["size"]

    def redraw_guide():
        size = guide["size"]
        canvas.config(width=size, height=size)
        canvas.delete("all")

        canvas.create_rectangle(
            2,
            2,
            size - 2,
            size - 2,
            outline="red",
            width=4
        )

        canvas.create_line(
            size // 2,
            0,
            size // 2,
            size,
            fill="red",
            width=2
        )

        canvas.create_line(
            0,
            size // 2,
            size,
            size // 2,
            fill="red",
            width=2
        )

        canvas.create_text(
            size // 2,
            28,
            text="Arrow keys move (Shift = faster). ⌘+ / ⌘− resize.",
            fill="black",
            font=("Arial", 14, "bold")
        )

        canvas.create_text(
            size // 2,
            56,
            text=f"{size} x {size}  |  Enter/Space: Start  |  Esc: Cancel",
            fill="black",
            font=("Arial", 11)
        )

        button_frame.place(
            x=max(size // 2 - 145, 5),
            y=max(size - 45, 5)
        )

        sync_final_region()

    def resize_guide(delta):
        old_size = guide["size"]
        new_size = max(
            GUIDE_MIN_SIZE,
            min(GUIDE_MAX_SIZE, old_size + delta)
        )

        if new_size == old_size:
            return

        # Keep the center of the guide roughly in the same place while resizing.
        current_left = root.winfo_x()
        current_top = root.winfo_y()
        center_x = current_left + old_size // 2
        center_y = current_top + old_size // 2

        guide["size"] = new_size
        new_left = int(center_x - new_size // 2)
        new_top = int(center_y - new_size // 2)

        root.geometry(f"{new_size}x{new_size}+{new_left}+{new_top}")
        redraw_guide()

    def move_guide(delta_x, delta_y):
        """Move the guide without changing its size."""
        new_left = root.winfo_x() + delta_x
        new_top = root.winfo_y() + delta_y

        guide["left"] = new_left
        guide["top"] = new_top
        root.geometry(f"+{new_left}+{new_top}")
        sync_final_region()

    def confirm():
        sync_final_region()

        root.withdraw()
        root.update_idletasks()
        root.update()
        time.sleep(0.6)

        root.destroy()

    def cancel():
        cancelled["value"] = True
        root.destroy()

    tk.Button(
        button_frame,
        text="−",
        command=lambda: resize_guide(-GUIDE_SIZE_STEP),
        font=("Arial", 12, "bold"),
        width=3
    ).pack(side="left", padx=3)

    tk.Button(
        button_frame,
        text="+",
        command=lambda: resize_guide(GUIDE_SIZE_STEP),
        font=("Arial", 12, "bold"),
        width=3
    ).pack(side="left", padx=3)

    tk.Button(
        button_frame,
        text="Start",
        command=confirm,
        font=("Arial", 12, "bold")
    ).pack(side="left", padx=5)

    tk.Button(
        button_frame,
        text="Cancel",
        command=cancel,
        font=("Arial", 12)
    ).pack(side="left", padx=5)

    def start_drag(event):
        drag_data["x"] = event.x
        drag_data["y"] = event.y

    def drag_window(event):
        x = root.winfo_x() + event.x - drag_data["x"]
        y = root.winfo_y() + event.y - drag_data["y"]

        root.geometry(f"+{x}+{y}")
        sync_final_region()

    def on_key(event):
        move_step = (
            GUIDE_FAST_MOVE_STEP
            if event.state & 0x0001
            else GUIDE_MOVE_STEP
        )

        if event.keysym == "Left":
            move_guide(-move_step, 0)
        elif event.keysym == "Right":
            move_guide(move_step, 0)
        elif event.keysym == "Up":
            move_guide(0, -move_step)
        elif event.keysym == "Down":
            move_guide(0, move_step)
        elif event.keysym in {"plus", "equal", "KP_Add", "bracketright"}:
            resize_guide(GUIDE_SIZE_STEP)
        elif event.keysym in {"minus", "underscore", "KP_Subtract", "bracketleft"}:
            resize_guide(-GUIDE_SIZE_STEP)
        elif event.keysym in {"Return", "space"}:
            confirm()
        elif event.keysym == "Escape":
            cancel()

        # Prevent focused buttons from also handling Space/Return.
        return "break"

    def on_mouse_wheel(event):
        # Windows/macOS: event.delta. Linux may use Button-4/Button-5 below.
        if getattr(event, "delta", 0) > 0:
            resize_guide(GUIDE_SIZE_STEP)
        elif getattr(event, "delta", 0) < 0:
            resize_guide(-GUIDE_SIZE_STEP)

    def grow_guide(event=None):
        resize_guide(GUIDE_SIZE_STEP)
        return "break"

    def shrink_guide(event=None):
        resize_guide(-GUIDE_SIZE_STEP)
        return "break"

    canvas.bind("<ButtonPress-1>", start_drag)
    canvas.bind("<B1-Motion>", drag_window)
    root.bind("<Key>", on_key)
    # On macOS, Command-plus is commonly reported by Tk as Command-Shift-equal.
    # Bind both forms explicitly so the shortcut works across Tk versions and
    # keyboard layouts.
    root.bind("<Command-plus>", grow_guide)
    root.bind("<Command-equal>", grow_guide)
    root.bind("<Command-Shift-equal>", grow_guide)
    root.bind("<Command-minus>", shrink_guide)
    root.bind("<Command-KP_Add>", grow_guide)
    root.bind("<Command-KP_Subtract>", shrink_guide)
    root.bind("<MouseWheel>", on_mouse_wheel)
    root.bind("<Button-4>", lambda event: resize_guide(GUIDE_SIZE_STEP))
    root.bind("<Button-5>", lambda event: resize_guide(-GUIDE_SIZE_STEP))

    redraw_guide()
    root.focus_force()
    root.mainloop()

    if cancelled["value"]:
        raise RuntimeError("Capture cancelled.")

    return final_region

def remove_red_crosshairs(img):
    img = img.convert("RGB")
    arr = np.array(img)

    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]

    red_like = (
        (r > 150)
        & (g < 150)
        & (b < 180)
        & (r > g * 1.2)
        & (r > b * 1.2)
    )

    arr[red_like] = [255, 255, 255]

    return Image.fromarray(arr)


def point_is_outside_region(x, y, region, margin=10):
    """Return True if the point is not inside or too close to the capture box."""
    left = region["left"] - margin
    top = region["top"] - margin
    right = region["left"] + region["width"] + margin
    bottom = region["top"] + region["height"] + margin

    return not (left <= x <= right and top <= y <= bottom)


def move_mouse_away_from_capture(region):
    screen_w, screen_h = pyautogui.size()

    safe_min = 10
    safe_max_x = max(safe_min, screen_w - 10)
    safe_max_y = max(safe_min, screen_h - 10)

    left = region["left"]
    top = region["top"]
    right = region["left"] + region["width"]
    bottom = region["top"] + region["height"]

    candidates = [
        (right + 40, top + 20),
        (left - 40, top + 20),
        (left + 20, bottom + 40),
        (left + 20, top - 40),
        (safe_max_x, safe_max_y),
    ]

    for x, y in candidates:
        x = int(max(safe_min, min(x, safe_max_x)))
        y = int(max(safe_min, min(y, safe_max_y)))

        if point_is_outside_region(x, y, region):
            pyautogui.moveTo(x, y, duration=0.05)
            time.sleep(0.15)
            return

    # If the capture area somehow covers almost the whole screen, still move
    # away from the current letter area as much as possible.
    pyautogui.moveTo(safe_max_x, safe_max_y, duration=0.05)
    time.sleep(0.15)


def capture_screen_region(region):
    with mss.MSS() as sct:
        screenshot = sct.grab(region)

        img = Image.frombytes(
            "RGB",
            screenshot.size,
            screenshot.rgb
        )

    img = remove_red_crosshairs(img)
    return img


def auto_crop_white_card_with_offset(img):
    img = img.convert("RGB")
    arr = np.array(img)

    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]

    white_mask = (r > 220) & (g > 220) & (b > 220)

    ys, xs = np.where(white_mask)

    if len(xs) == 0 or len(ys) == 0:
        return img, 0, 0

    left = max(xs.min() - 5, 0)
    top = max(ys.min() - 5, 0)
    right = min(xs.max() + 5, arr.shape[1])
    bottom = min(ys.max() + 5, arr.shape[0])

    cropped = img.crop((left, top, right, bottom))

    return cropped, left, top


def auto_crop_white_card(img):
    cropped, _, _ = auto_crop_white_card_with_offset(img)
    return cropped


def otsu_threshold(gray_img):
    arr = np.array(gray_img)

    hist, _ = np.histogram(arr.flatten(), bins=256, range=(0, 256))
    total = arr.size

    sum_total = np.dot(np.arange(256), hist)

    sum_bg = 0
    weight_bg = 0
    max_var = 0
    threshold = 190

    for t in range(256):
        weight_bg += hist[t]

        if weight_bg == 0:
            continue

        weight_fg = total - weight_bg

        if weight_fg == 0:
            break

        sum_bg += t * hist[t]

        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg

        between_var = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2

        if between_var > max_var:
            max_var = between_var
            threshold = t

    return threshold


def crop_to_letter_bounds(bw_img, padding_ratio=0.18):
    arr = np.array(bw_img)

    dark = arr < 128
    ys, xs = np.where(dark)

    if len(xs) == 0 or len(ys) == 0:
        return bw_img

    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()

    letter_w = x_max - x_min + 1
    letter_h = y_max - y_min + 1

    pad_x = int(letter_w * padding_ratio) + 8
    pad_y = int(letter_h * padding_ratio) + 8

    left = max(x_min - pad_x, 0)
    top = max(y_min - pad_y, 0)
    right = min(x_max + pad_x + 1, arr.shape[1])
    bottom = min(y_max + pad_y + 1, arr.shape[0])

    return bw_img.crop((left, top, right, bottom))


def pad_to_square(img, fill=255):
    w, h = img.size
    size = max(w, h)

    square = Image.new("L", (size, size), fill)

    x = (size - w) // 2
    y = (size - h) // 2

    square.paste(img, (x, y))
    return square


def clean_cell_for_ocr(cell_img, threshold="otsu"):
    w, h = cell_img.size

    margin_x = int(w * 0.08)
    margin_y = int(h * 0.08)

    cell_img = cell_img.crop(
        (
            margin_x,
            margin_y,
            w - margin_x,
            h - margin_y
        )
    )

    gray = ImageOps.grayscale(cell_img)

    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(3.5)
    gray = gray.filter(ImageFilter.SHARPEN)

    if threshold == "otsu":
        threshold_value = otsu_threshold(gray)
    else:
        threshold_value = int(threshold)

    bw = gray.point(lambda p: 0 if p < threshold_value else 255)

    bw = bw.filter(ImageFilter.MedianFilter(size=3))
    bw = crop_to_letter_bounds(bw)
    bw = pad_to_square(bw)

    bw = bw.resize((240, 240), Image.Resampling.LANCZOS)

    return bw


def looks_like_capital_i(cell_img):
    cleaned = clean_cell_for_ocr(cell_img, threshold=210)
    arr = np.array(cleaned)

    dark = arr < 80
    ys, xs = np.where(dark)

    if len(xs) == 0 or len(ys) == 0:
        return False

    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()

    letter_width = x_max - x_min + 1
    letter_height = y_max - y_min + 1

    img_h, img_w = arr.shape

    tall_enough = letter_height > img_h * 0.35
    narrow_enough = letter_width < img_w * 0.20

    center_x = (x_min + x_max) / 2
    centered = img_w * 0.35 < center_x < img_w * 0.65

    enough_dark_pixels = len(xs) > 60

    return tall_enough and narrow_enough and centered and enough_dark_pixels


def get_normalized_letter_mask(cell_img, threshold="otsu", dark_cutoff=80, size=64):
    cleaned = clean_cell_for_ocr(cell_img, threshold=threshold)
    arr = np.array(cleaned)

    dark = arr < dark_cutoff
    ys, xs = np.where(dark)

    if len(xs) == 0 or len(ys) == 0:
        return None, None

    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()

    width = x_max - x_min + 1
    height = y_max - y_min + 1

    if width <= 0 or height <= 0:
        return None, None

    letter = dark[y_min:y_max + 1, x_min:x_max + 1]
    letter_img = Image.fromarray((letter * 255).astype(np.uint8))
    letter_img = letter_img.resize((size, size))

    return np.array(letter_img) > 0, width / height


def looks_like_capital_p(cell_img):
    """
    Backup detector for capital P.
    Helps distinguish P from F by checking:
    - strong left vertical stem
    - top horizontal stroke
    - middle horizontal stroke
    - right-side upper bowl
    - mostly empty lower-right area
    """
    letter, aspect_ratio = get_normalized_letter_mask(
        cell_img,
        threshold=190,
        dark_cutoff=80
    )

    if letter is None:
        return False

    # P should be reasonably wide because of the bowl.
    # F can be narrower or less filled on the right.
    if aspect_ratio < 0.35:
        return False

    left_stem = letter[:, 0:16]

    top_bar = letter[0:18, 0:52]
    middle_bar = letter[24:42, 0:52]

    upper_right_bowl = letter[8:34, 34:64]
    mid_right_bowl = letter[18:42, 34:64]

    lower_right = letter[42:64, 28:64]
    lower_left = letter[42:64, 0:22]

    far_right_upper = letter[10:34, 50:64]
    far_right_lower = letter[42:64, 50:64]

    left_stem_density = left_stem.mean()
    top_bar_density = top_bar.mean()
    middle_bar_density = middle_bar.mean()
    upper_right_bowl_density = upper_right_bowl.mean()
    mid_right_bowl_density = mid_right_bowl.mean()
    lower_right_density = lower_right.mean()
    lower_left_density = lower_left.mean()
    far_right_upper_density = far_right_upper.mean()
    far_right_lower_density = far_right_lower.mean()

    has_left_stem = left_stem_density > 0.22
    has_top_bar = top_bar_density > 0.12
    has_middle_bar = middle_bar_density > 0.10

    upper_right_stronger_than_lower = (
        far_right_upper_density > far_right_lower_density + 0.025
    )

    # Key P-vs-F check:
    # P should have dark pixels on the upper far right from the bowl.
    # F usually has much less there.
    has_upper_right_bowl = (
        upper_right_bowl_density > 0.08
        and mid_right_bowl_density > 0.06
        and far_right_upper_density > 0.05
        and upper_right_stronger_than_lower
    )

    # Lower right should be mostly empty for P.
    lower_right_clear = lower_right_density < 0.08
    far_right_lower_clear = far_right_lower_density < 0.05

    # P keeps the left stem going down.
    has_lower_stem = lower_left_density > 0.08

    return (
        has_left_stem
        and has_top_bar
        and has_middle_bar
        and has_upper_right_bowl
        and lower_right_clear
        and far_right_lower_clear
        and has_lower_stem
    )


def looks_like_capital_f(cell_img):
    """
    Backup detector for capital F.
    It is intentionally conservative: strong left stem and top/middle bars,
    but no P-style upper-right bowl or lower-right stroke.
    """
    letter, aspect_ratio = get_normalized_letter_mask(
        cell_img,
        threshold=190,
        dark_cutoff=80
    )

    if letter is None:
        return False

    if aspect_ratio < 0.25:
        return False

    left_stem = letter[:, 0:16]
    top_bar = letter[0:18, 0:52]
    middle_bar = letter[24:42, 0:52]
    lower_left = letter[42:64, 0:22]

    upper_right_bowl = letter[8:34, 34:64]
    far_right_upper = letter[10:34, 50:64]
    far_right_lower = letter[42:64, 50:64]
    lower_right = letter[42:64, 28:64]
    bottom_bar = letter[48:64, 0:52]

    left_stem_density = left_stem.mean()
    top_bar_density = top_bar.mean()
    middle_bar_density = middle_bar.mean()
    lower_left_density = lower_left.mean()
    upper_right_bowl_density = upper_right_bowl.mean()
    far_right_upper_density = far_right_upper.mean()
    far_right_lower_density = far_right_lower.mean()
    lower_right_density = lower_right.mean()
    bottom_bar_density = bottom_bar.mean()

    has_left_stem = left_stem_density > 0.22
    has_top_bar = top_bar_density > 0.12
    has_middle_bar = middle_bar_density > 0.10
    has_lower_stem = lower_left_density > 0.08

    no_upper_bowl = (
        upper_right_bowl_density < 0.14
        and far_right_upper_density < 0.07
    )
    lower_right_clear = lower_right_density < 0.08
    far_right_lower_clear = far_right_lower_density < 0.05
    no_bottom_bar = bottom_bar_density < 0.13

    return (
        has_left_stem
        and has_top_bar
        and has_middle_bar
        and has_lower_stem
        and no_upper_bowl
        and lower_right_clear
        and far_right_lower_clear
        and no_bottom_bar
    )


def looks_like_capital_o(cell_img):
    letter, aspect_ratio = get_normalized_letter_mask(
        cell_img,
        threshold="otsu",
        dark_cutoff=90
    )

    if letter is None:
        return False

    if not (0.70 <= aspect_ratio <= 1.30):
        return False

    left_band = letter[:, 0:16]
    right_band = letter[:, 48:64]
    top_band = letter[0:16, :]
    bottom_band = letter[48:64, :]
    center = letter[22:42, 22:42]
    lower_right_tail = letter[42:64, 42:64]
    upper_left_corner = letter[0:16, 0:16]
    upper_right_corner = letter[0:16, 48:64]
    lower_left_corner = letter[48:64, 0:16]
    lower_right_corner = letter[48:64, 48:64]

    left_density = left_band.mean()
    right_density = right_band.mean()
    top_density = top_band.mean()
    bottom_density = bottom_band.mean()
    center_density = center.mean()
    tail_density = lower_right_tail.mean()
    corner_densities = [
        upper_left_corner.mean(),
        upper_right_corner.mean(),
        lower_left_corner.mean(),
        lower_right_corner.mean()
    ]

    has_left_right = left_density > 0.10 and right_density > 0.10
    has_top_bottom = top_density > 0.08 and bottom_density > 0.08
    clear_center = center_density < 0.12
    no_heavy_tail = tail_density < 0.45

    balanced_sides = abs(left_density - right_density) < 0.20
    balanced_top_bottom = abs(top_density - bottom_density) < 0.20
    rounded_corners = max(corner_densities) < 0.55

    return (
        has_left_right
        and has_top_bottom
        and clear_center
        and no_heavy_tail
        and balanced_sides
        and balanced_top_bottom
        and rounded_corners
    )



def normalize_image_for_backup_matching(img):
    """
    Normalize a saved backup/template image or a fresh cell image into
    a comparable black-ink-on-white-background mask.

    This intentionally mirrors the OCR cleanup style while being tolerant
    of whether the backup image is an original crop or an already-cleaned
    template image.
    """
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray)

    threshold_value = otsu_threshold(gray)
    bw = gray.point(lambda p: 0 if p < threshold_value else 255)

    bw = bw.filter(ImageFilter.MedianFilter(size=3))
    bw = crop_to_letter_bounds(bw)
    bw = pad_to_square(bw)
    bw = bw.resize(
        (BACKUP_MATCH_IMAGE_SIZE, BACKUP_MATCH_IMAGE_SIZE),
        Image.Resampling.LANCZOS
    )

    arr = np.array(bw).astype(np.float32)

    # Convert to ink intensity: black letter pixels become high values.
    ink = 1.0 - (arr / 255.0)

    # Ignore nearly blank templates.
    if ink.sum() < 10:
        return None

    # Normalize brightness/weight so heavier letters do not always win.
    norm = np.linalg.norm(ink)
    if norm == 0:
        return None

    return ink / norm


def backup_template_sort_key(path):
    """
    Prefer cleaned high-confidence templates first, then newest-ish names.
    Filenames already include timestamps from get_template_output_path.
    """
    name = os.path.basename(path).lower()

    cleaned_bonus = 0 if "_cleaned_" in name else 1
    low_conf_penalty = 1 if "low_confidence" in path.lower() else 0

    return (low_conf_penalty, cleaned_bonus, name)


@lru_cache(maxsize=1)
def load_backup_letter_templates():
    """
    Load saved backup images from folders like:

        ocr_letter_templates/high_confidence/A/*.png
        ocr_letter_templates/high_confidence/B/*.png

    Returns:
        list[tuple[str, np.ndarray, str]]
        where each item is (letter, normalized_image_vector, path).
    """
    templates = []

    if not USE_BACKUP_IMAGE_MATCHING:
        return templates

    if not os.path.isdir(BACKUP_IMAGE_ROOT):
        return templates

    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        letter_dir = os.path.join(BACKUP_IMAGE_ROOT, letter)

        if not os.path.isdir(letter_dir):
            continue

        image_paths = [
            os.path.join(letter_dir, filename)
            for filename in os.listdir(letter_dir)
            if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]

        image_paths = sorted(image_paths, key=backup_template_sort_key)
        image_paths = image_paths[:BACKUP_MAX_TEMPLATES_PER_LETTER]

        for path in image_paths:
            try:
                template_img = Image.open(path)
                normalized = normalize_image_for_backup_matching(template_img)

                if normalized is not None:
                    templates.append((letter, normalized, path))
            except Exception:
                # Ignore bad/corrupt images and keep the OCR pipeline running.
                continue

    return templates


def match_cell_with_backup_images(cell_img):
    """
    Try to identify a cell by comparing it to saved backup/template images.

    Returns:
        (letter, confidence, score, template_path)

    If no good match is found:
        ("?", -1, -1, None)
    """
    templates = load_backup_letter_templates()

    if not templates:
        return "?", -1, -1, None

    # Use the same cleaned OCR image so matching sees the letter, not the tile.
    query_img = clean_cell_for_ocr(cell_img, threshold="otsu")
    query = normalize_image_for_backup_matching(query_img)

    if query is None:
        return "?", -1, -1, None

    best_letter = "?"
    best_score = -1.0
    best_path = None

    second_best_score = -1.0

    for letter, template, path in templates:
        score = float(np.sum(query * template))

        if score > best_score:
            second_best_score = best_score
            best_letter = letter
            best_score = score
            best_path = path
        elif score > second_best_score:
            second_best_score = score

    margin = best_score - second_best_score

    if best_score >= BACKUP_MATCH_THRESHOLD and margin >= BACKUP_MATCH_MARGIN:
        # Convert a 0-1 similarity score into a confidence-like number.
        confidence = min(98, max(70, int(round(best_score * 100))))
        return best_letter, confidence, best_score, best_path

    return "?", -1, best_score, best_path


def maybe_use_backup_image_match(cell_img, letter, confidence):
    """
    Use template image matching after OCR only when OCR is unknown or low confidence.
    This preserves the old fallback behavior when template-first matching fails.
    """
    if not USE_BACKUP_IMAGE_MATCHING:
        return letter, confidence

    if letter != "?" and confidence >= BACKUP_MATCH_WHEN_CONF_BELOW:
        return letter, confidence

    backup_letter, backup_conf, backup_score, backup_path = match_cell_with_backup_images(
        cell_img
    )

    if backup_letter != "?":
        print(
            f"Backup image match used after OCR: {backup_letter} "
            f"(score={backup_score:.3f}, conf={backup_conf}, "
            f"previous={letter}/{confidence:.1f})"
        )
        return backup_letter, backup_conf

    return letter, confidence


def maybe_use_template_image_match_first(cell_img):
    """
    Try template image matching before OCR.

    Returns:
        (letter, confidence) if a confident template match is found.
        (None, None) if OCR should run instead.
    """
    if not USE_BACKUP_IMAGE_MATCHING or not USE_TEMPLATE_MATCHING_FIRST:
        return None, None

    backup_letter, backup_conf, backup_score, backup_path = match_cell_with_backup_images(
        cell_img
    )

    if backup_letter != "?" and backup_score >= TEMPLATE_FIRST_MATCH_THRESHOLD:
        print(
            f"Template image match used before OCR: {backup_letter} "
            f"(score={backup_score:.3f}, conf={backup_conf})"
        )
        return backup_letter, backup_conf

    return None, None

def ocr_attempt(cell_img, thresholds, psm_modes):
    best_letter = "?"
    best_conf = -1

    for threshold in thresholds:
        cleaned = clean_cell_for_ocr(cell_img, threshold=threshold)

        for psm in psm_modes:
            config = OCR_CONFIG_TEMPLATE.format(psm=psm)

            data = pytesseract.image_to_data(
                cleaned,
                config=config,
                output_type=pytesseract.Output.DICT
            )

            for text, conf in zip(data.get("text", []), data.get("conf", [])):
                text = text.upper().replace("0", "O")
                text = re.sub(r"[^A-Z]", "", text)

                try:
                    conf = float(conf)
                except ValueError:
                    conf = -1

                if len(text) == 1 and conf > best_conf:
                    best_letter = text
                    best_conf = conf

    return best_letter, best_conf


def ocr_single_letter(cell_img):
    template_letter, template_conf = maybe_use_template_image_match_first(cell_img)
    if template_letter is not None:
        return template_letter, template_conf

    if looks_like_capital_i(cell_img):
        return "I", 99

    letter, conf = ocr_attempt(
        cell_img,
        FAST_THRESHOLDS,
        PSM_MODES_FAST
    )

    if letter in {"?", "D", "C", "Q"} or conf < 70:
        if looks_like_capital_o(cell_img):
            return "O", 96

    if letter != "?" and conf >= 65 and letter not in {"F", "R", "D", "B", "C", "Q"}:
        return letter, conf

    if letter in {"?", "P", "F", "R", "D", "B"} or conf < 65:
        looks_like_p = looks_like_capital_p(cell_img)
        looks_like_f = looks_like_capital_f(cell_img)

        if looks_like_p:
            return "P", 96

        # If OCR guessed P but the P-shape check fails,
        # it is often actually F.
        if looks_like_f:
            return "F", 94

        if letter == "P":
            return "F", min(conf, 70)

    letter2, conf2 = ocr_attempt(
        cell_img,
        FALLBACK_THRESHOLDS,
        PSM_MODES_FALLBACK
    )

    if letter2 in {"?", "D", "C", "Q"} or conf2 < 70:
        if looks_like_capital_o(cell_img):
            return "O", 96

    if letter2 in {"?", "P", "F", "R", "D", "B"} or conf2 < 60:
        looks_like_p = looks_like_capital_p(cell_img)
        looks_like_f = looks_like_capital_f(cell_img)

        if looks_like_p:
            return "P", 96

        if looks_like_f:
            return "F", 94

        if letter2 == "P":
            return "F", min(conf2, 70)

    if conf2 > conf:
        return maybe_use_backup_image_match(cell_img, letter2, conf2)

    return maybe_use_backup_image_match(cell_img, letter, conf)


def safe_letter_folder_name(letter):
    if len(letter) == 1 and letter.isalpha():
        return letter.upper()

    return "UNKNOWN"


def get_template_output_path(letter, confidence, row, col, image_kind):
    letter_folder = safe_letter_folder_name(letter)

    if confidence >= HIGH_CONFIDENCE_THRESHOLD and letter_folder != "UNKNOWN":
        confidence_folder = "high_confidence"
    else:
        confidence_folder = "low_confidence"

    output_dir = os.path.join(
        TEMPLATE_IMAGE_ROOT,
        confidence_folder,
        letter_folder
    )

    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    conf_text = f"{int(confidence)}" if confidence is not None else "unknown"

    filename = (
        f"{letter_folder}_conf_{conf_text}_"
        f"row_{row}_col_{col}_{image_kind}_{timestamp}.png"
    )

    return os.path.join(output_dir, filename)


def save_letter_template_images(cell_img, letter, confidence, row, col):
    if not SAVE_TEMPLATE_IMAGES:
        return

    if SAVE_CLEANED_TEMPLATE_IMAGE:
        cleaned = clean_cell_for_ocr(cell_img, threshold="otsu")
        cleaned_path = get_template_output_path(
            letter=letter,
            confidence=confidence,
            row=row,
            col=col,
            image_kind="cleaned"
        )
        cleaned.save(cleaned_path)

    if SAVE_ORIGINAL_TEMPLATE_IMAGE:
        original_path = get_template_output_path(
            letter=letter,
            confidence=confidence,
            row=row,
            col=col,
            image_kind="original"
        )
        cell_img.save(original_path)


def split_grid_into_cells(img, capture_region=None):
    img, crop_left, crop_top = auto_crop_white_card_with_offset(img)

    width, height = img.size

    pad_x = int(width * 0.04)
    pad_y = int(height * 0.04)

    img = img.crop(
        (
            pad_x,
            pad_y,
            width - pad_x,
            height - pad_y
        )
    )

    width, height = img.size
    cell_w = width / GRID_SIZE
    cell_h = height / GRID_SIZE

    cells = []
    cell_centers_screen = {}

    screen_left = 0
    screen_top = 0

    if capture_region is not None:
        screen_left = capture_region["left"]
        screen_top = capture_region["top"]

    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            left = int(col * cell_w)
            top = int(row * cell_h)
            right = int((col + 1) * cell_w)
            bottom = int((row + 1) * cell_h)

            cell_img = img.crop((left, top, right, bottom))
            cells.append((row, col, cell_img))

            center_x = (
                screen_left
                + crop_left
                + pad_x
                + left
                + (right - left) / 2
            )

            center_y = (
                screen_top
                + crop_top
                + pad_y
                + top
                + (bottom - top) / 2
            )

            cell_centers_screen[(row, col)] = (int(center_x), int(center_y))

    return cells, cell_centers_screen


def extract_5x5_grid_from_image(img, capture_region=None):
    cells, cell_centers_screen = split_grid_into_cells(
        img,
        capture_region=capture_region
    )

    grid = [["?" for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    confidences = [[-1 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]

    max_workers = min(8, os.cpu_count() or 4)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(ocr_single_letter, cell_img): (row, col, cell_img)
            for row, col, cell_img in cells
        }

        for future in as_completed(future_map):
            row, col, cell_img = future_map[future]

            try:
                letter, conf = future.result()
            except Exception:
                letter, conf = "?", -1

            grid[row][col] = letter
            confidences[row][col] = conf

            save_letter_template_images(
                cell_img=cell_img,
                letter=letter,
                confidence=conf,
                row=row,
                col=col
            )

            if SAVE_FAILED_CELLS and (letter == "?" or conf < 30):
                cell_img.save(f"debug_failed_cell_{row}_{col}.png")

    if ASK_FOR_UNKNOWN_CELLS:
        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                if grid[row][col] == "?" or confidences[row][col] < 20:
                    val = input(
                        f"Cell ({row}, {col}) was '{grid[row][col]}' "
                        f"with confidence {confidences[row][col]:.1f}. Enter letter: "
                    ).strip().upper()

                    if len(val) == 1 and val.isalpha():
                        grid[row][col] = val
                        confidences[row][col] = 100

    return grid, confidences, cell_centers_screen


def find_5_letter_words(grid):
    directions = [
        (0, 1),
        (0, -1),
        (1, 0),
        (-1, 0),
        (1, 1),
        (1, -1),
        (-1, 1),
        (-1, -1),
    ]

    results = []

    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            for dr, dc in directions:
                word = ""
                positions = []

                for i in range(5):
                    nr = r + dr * i
                    nc = c + dc * i

                    if nr < 0 or nr >= GRID_SIZE or nc < 0 or nc >= GRID_SIZE:
                        break

                    word += grid[nr][nc]
                    positions.append((nr, nc))

                if len(word) == 5 and "?" not in word and is_english_word(word):
                    results.append({
                        "word": word,
                        "start": positions[0],
                        "end": positions[-1],
                        "positions": positions
                    })

    unique = {}

    for item in results:
        key = (item["word"], tuple(item["positions"]))
        unique[key] = item

    return list(unique.values())


def trace_word_on_screen(word_item, cell_centers_screen):
    positions = word_item["positions"]

    points = [
        cell_centers_screen[pos]
        for pos in positions
        if pos in cell_centers_screen
    ]

    if len(points) != len(positions):
        print("Could not trace word because some cell positions are missing.")
        return

    word = word_item["word"]

    print(f"\nTracing word: {word}")
    print(f"Path: {points}")

    start_x, start_y = points[0]

    pyautogui.moveTo(
        start_x,
        start_y,
        duration=TRACE_MOVE_DURATION
    )

    pyautogui.mouseDown()

    for x, y in points[1:]:
        pyautogui.dragTo(
            x,
            y,
            duration=TRACE_DRAG_DURATION,
            button="left"
        )

    pyautogui.mouseUp()

    time.sleep(TRACE_PAUSE_AFTER_WORD)


def print_grid(grid, confidences=None):
    print("\nDetected grid:")
    for row in grid:
        print(" ".join(row))

    if confidences:
        print("\nConfidence:")
        for row in confidences:
            print(" ".join(f"{int(c):2d}" for c in row))


def run_auto_capture():
    global CAPTURE_REGION

    print("Align the guide around the full 5x5 letter card, then click Start.")
    CAPTURE_REGION = show_capture_guide(CAPTURE_REGION)

    print("\nUsing capture region:")
    print(CAPTURE_REGION)

    print(f"\nAuto-capturing every {AUTO_CAPTURE_SECONDS} seconds.")
    print("Press Ctrl+C to stop.")
    print("Move mouse to the top-left corner to stop pyautogui actions.")

    if SAVE_TEMPLATE_IMAGES:
        print(f"Saving template images under: {TEMPLATE_IMAGE_ROOT}")

    print()

    try:
        while True:
            start_time = time.time()

            move_mouse_away_from_capture(CAPTURE_REGION)
            img = capture_screen_region(CAPTURE_REGION)

            grid, confidences, cell_centers_screen = extract_5x5_grid_from_image(
                img,
                capture_region=CAPTURE_REGION
            )

            words = find_5_letter_words(grid)

            print_grid(grid, confidences)

            print("\n5-letter words found:")
            if words:
                for item in words:
                    print(
                        f"{item['word']} | "
                        f"start={item['start']} | "
                        f"end={item['end']}"
                    )
            else:
                print("None found.")

            if AUTO_TRACE_FOUND_WORD and words:
                if TRACE_ONLY_FIRST_WORD:
                    trace_word_on_screen(words[0], cell_centers_screen)
                else:
                    for item in words:
                        trace_word_on_screen(item, cell_centers_screen)

                move_mouse_away_from_capture(CAPTURE_REGION)

            elapsed = time.time() - start_time
            print(f"\nScan time: {elapsed:.2f}s")
            print("-" * 40)

            sleep_time = max(0, AUTO_CAPTURE_SECONDS - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    run_auto_capture()
