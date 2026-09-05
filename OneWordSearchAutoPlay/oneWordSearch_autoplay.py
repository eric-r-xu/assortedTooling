from PIL import Image, ImageOps, ImageEnhance, ImageFilter
from collections import OrderedDict
import hashlib
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
from threading import Lock
from wordfreq import zipf_frequency


GRID_SIZE = 5
AUTO_CAPTURE_SECONDS = 0.25

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
FAST_THRESHOLDS = ["otsu"]
FALLBACK_THRESHOLDS = ["otsu"]

# White-letter isolation settings. The game can draw white letters over
# differently colored tiles, so color and saturation should not become part of
# the glyph passed to OCR. These values deliberately include anti-aliased edge
# pixels, not just pure #FFFFFF pixels.
WHITE_TEXT_MIN_SCORE = 130
WHITE_TEXT_MAX_CHROMA = 110
WHITE_TEXT_MIN_COVERAGE = 0.003
WHITE_TEXT_MAX_COVERAGE = 0.40

# Dark-board grid detection. On the dark game theme the card itself contains
# no large white area, so auto_crop_white_card() would mistake the letters for
# the card boundaries. Instead, five regularly spaced bands of neutral-white
# pixels reveal the row and column centers directly.
DARK_GRID_WHITE_MIN = 180
DARK_GRID_WHITE_MAX_CHROMA = 40
DARK_GRID_PROJECTION_FRACTION = 0.05
DARK_GRID_MAX_SPACING_VARIATION = 0.15

PSM_MODES_FAST = [10]
# PSM 13 is retained as the single fallback because the game's R glyph is not
# recognized reliably by PSM 10. Repeating thresholds is unnecessary after
# white-letter isolation has already produced a binary image.
PSM_MODES_FALLBACK = [13]

ASK_FOR_UNKNOWN_CELLS = False
SAVE_FAILED_CELLS = False

# Template-saving settings
SAVE_TEMPLATE_IMAGES = False
TEMPLATE_IMAGE_ROOT = "ocr_letter_templates_oneWordSearch"

# Confident template matches avoid starting a Tesseract process for that cell.
# With no saved templates these settings add no recognition work and OCR runs
# exactly as before.
USE_BACKUP_IMAGE_MATCHING = True
USE_TEMPLATE_MATCHING_FIRST = True
BACKUP_IMAGE_ROOT = os.path.join(TEMPLATE_IMAGE_ROOT, "high_confidence")
BACKUP_MATCH_WHEN_CONF_BELOW = 65
BACKUP_MATCH_THRESHOLD = 0.72
TEMPLATE_FIRST_MATCH_THRESHOLD = 0.78
BACKUP_MATCH_MARGIN = 0.035
# A compact index is both faster to load and sufficient for this fixed game
# font. Ambiguous matches retain the existing Tesseract fallback.
BACKUP_MAX_TEMPLATES_PER_LETTER = 12
BACKUP_MATCH_IMAGE_SIZE = 96
OCR_RESULT_CACHE_SIZE = 512
NORMALIZED_GLYPH_CACHE_SIZE = 512
OCR_MAX_WORKERS = min(8, os.cpu_count() or 4)

HIGH_CONFIDENCE_THRESHOLD = 80
LOW_CONFIDENCE_THRESHOLD = 80

SAVE_CLEANED_TEMPLATE_IMAGE = True
SAVE_ORIGINAL_TEMPLATE_IMAGE = False

# Auto-click/trace settings
AUTO_TRACE_FOUND_WORD = True
TRACE_ONLY_FIRST_WORD = False

# These remain long enough for the game to receive each cell crossing while
# avoiding unnecessary idle time between words.
TRACE_MOVE_DURATION = 0.02
TRACE_DRAG_DURATION = 0.03
TRACE_PAUSE_AFTER_WORD = 0.10

# Move mouse to top-left corner to abort pyautogui actions. Mouse events are
# synchronous, and the explicit sleeps below provide the settling time the game
# needs, so a 1 ms command pause avoids adding 10 ms after every point traced.
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.001

# MSS setup is relatively expensive. Keep one capture handle alive for the
# capture loop instead of creating and tearing down a session every scan.
_SCREEN_CAPTURE = None

# Exact cell pixels commonly repeat between captures. Cache their final result
# so a stable board pays recognition cost only once. This is deliberately an
# exact digest, not a perceptual hash, so visually similar letters cannot alias.
_OCR_RESULT_CACHE = OrderedDict()
_OCR_RESULT_CACHE_LOCK = Lock()
_NORMALIZED_GLYPH_RESULT_CACHE = OrderedDict()
_NORMALIZED_GLYPH_RESULT_CACHE_LOCK = Lock()

# Reusing the executor avoids starting and joining eight worker threads on
# every 250 ms capture cycle.
_OCR_EXECUTOR = None
_OCR_EXECUTOR_LOCK = Lock()

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


@lru_cache(maxsize=4096)
def is_english_word(word):
    return len(word) == 5 and zipf_frequency(word.lower(), "en") >= 2.5


def _build_word_lines():
    """Build every valid five-cell line once, in the legacy scan order."""
    directions = ((0, 1), (1, 0), (1, 1), (1, -1))
    lines = []

    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            for row_step, col_step in directions:
                end_row = row + row_step * (GRID_SIZE - 1)
                end_col = col + col_step * (GRID_SIZE - 1)
                if 0 <= end_row < GRID_SIZE and 0 <= end_col < GRID_SIZE:
                    lines.append(tuple(
                        (row + row_step * offset, col + col_step * offset)
                        for offset in range(GRID_SIZE)
                    ))

    return tuple(lines)


# Grid geometry never changes during play. Precomputing its 12 undirected
# lines removes the nested direction/bounds work from every capture while
# retaining the exact result order used to choose the first traced word.
WORD_LINES = _build_word_lines()


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
    current_x, current_y = pyautogui.position()
    if point_is_outside_region(current_x, current_y, region):
        return False

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
            return True

    # If the capture area somehow covers almost the whole screen, still move
    # away from the current letter area as much as possible.
    pyautogui.moveTo(safe_max_x, safe_max_y, duration=0.05)
    time.sleep(0.15)
    return True


def capture_screen_region(region):
    global _SCREEN_CAPTURE

    if _SCREEN_CAPTURE is None:
        _SCREEN_CAPTURE = mss.MSS()

    screenshot = _SCREEN_CAPTURE.grab(region)
    img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

    img = remove_red_crosshairs(img)
    return img


def close_screen_capture():
    """Release the reusable MSS session when the capture loop exits."""
    global _SCREEN_CAPTURE

    if _SCREEN_CAPTURE is not None:
        _SCREEN_CAPTURE.close()
        _SCREEN_CAPTURE = None


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


def _find_projection_centers(projection):
    """Return centers of the five substantial runs in a 1-D projection."""
    if projection.size == 0 or projection.max() <= 0:
        return None

    minimum = max(3, projection.max() * DARK_GRID_PROJECTION_FRACTION)
    active = np.flatnonzero(projection >= minimum)
    if active.size == 0:
        return None

    runs = []
    start = previous = int(active[0])
    for coordinate in active[1:]:
        coordinate = int(coordinate)
        if coordinate > previous + 1:
            runs.append((start, previous))
            start = coordinate
        previous = coordinate
    runs.append((start, previous))

    # Single-pixel noise can otherwise create extra bands near the board.
    runs = [(start, end) for start, end in runs if end - start + 1 >= 3]
    if len(runs) != GRID_SIZE:
        return None

    centers = np.array(
        [(start + end) / 2.0 for start, end in runs],
        dtype=np.float64,
    )
    spacings = np.diff(centers)
    median_spacing = float(np.median(spacings))
    if median_spacing <= 0:
        return None

    spacing_error = np.max(np.abs(spacings - median_spacing)) / median_spacing
    if spacing_error > DARK_GRID_MAX_SPACING_VARIATION:
        return None

    return centers


def detect_dark_grid_centers(img):
    """Detect 5x5 centers from white glyphs on the game's dark theme.

    Returns ``(x_centers, y_centers)`` in image coordinates, or ``None`` when
    the image does not contain a convincing regularly spaced dark-theme grid.
    """
    rgb = np.asarray(img.convert("RGB"), dtype=np.int16)
    channel_min = rgb.min(axis=2)
    chroma = rgb.max(axis=2) - channel_min
    white_glyphs = (
        (channel_min >= DARK_GRID_WHITE_MIN)
        & (chroma <= DARK_GRID_WHITE_MAX_CHROMA)
    )

    x_centers = _find_projection_centers(white_glyphs.sum(axis=0))
    y_centers = _find_projection_centers(white_glyphs.sum(axis=1))
    if x_centers is None or y_centers is None:
        return None

    # A real 5x5 board should occupy most of the selected capture region.
    height, width = white_glyphs.shape
    if (
        x_centers[-1] - x_centers[0] < width * 0.50
        or y_centers[-1] - y_centers[0] < height * 0.50
    ):
        return None

    return x_centers, y_centers


def otsu_threshold(gray_img):
    # Grayscale pixels are integers in the inclusive range 0..255.  bincount
    # gives us those exact 256 bins and avoids a NumPy histogram edge-case
    # seen with uint8 images on newer Python versions.
    arr = np.asarray(gray_img, dtype=np.uint8)
    hist = np.bincount(arr.ravel(), minlength=256)
    total = arr.size

    sum_total = np.dot(np.arange(256), hist)

    weights_bg = np.cumsum(hist)
    sums_bg = np.cumsum(np.arange(256) * hist)
    weights_fg = total - weights_bg

    valid = (weights_bg > 0) & (weights_fg > 0)
    if not np.any(valid):
        return 190

    # This is the algebraically equivalent between-class variance, evaluated
    # in one NumPy operation instead of a Python loop for every OCR cell.
    mean_delta = (
        sums_bg[valid] * weights_fg[valid]
        - (sum_total - sums_bg[valid]) * weights_bg[valid]
    )
    between_var = (
        mean_delta * mean_delta
        / (weights_bg[valid] * weights_fg[valid])
    )
    valid_thresholds = np.flatnonzero(valid)
    return int(valid_thresholds[np.argmax(between_var)])


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


def isolate_white_letters(cell_img):
    """Return white glyphs as black ink and flatten every other color.

    A pixel's score is driven mostly by its darkest RGB channel. This prevents
    bright saturated tile colors from looking white, while the chroma allowance
    keeps anti-aliased letter edges that have blended with the tile color.
    The relative threshold also lets white text remain detectable on light gray
    tiles. ``None`` means the cell does not look like a white-on-color cell, so
    the legacy dark-letter cleanup should be used instead.
    """
    rgb = np.asarray(cell_img.convert("RGB"), dtype=np.int16)
    channel_min = rgb.min(axis=2)
    channel_max = rgb.max(axis=2)
    chroma = channel_max - channel_min

    white_score = channel_min - (0.15 * chroma)
    background_score = float(np.median(white_score))
    brightest_score = float(np.percentile(white_score, 99))

    # Require pixels to be both absolutely light and noticeably closer to
    # white than the cell's dominant color.
    relative_margin = max(10.0, (brightest_score - background_score) * 0.20)
    score_threshold = max(
        float(WHITE_TEXT_MIN_SCORE),
        background_score + relative_margin,
    )
    white_mask = (
        (white_score >= score_threshold)
        & (chroma <= WHITE_TEXT_MAX_CHROMA)
    )

    coverage = float(white_mask.mean())
    if not (WHITE_TEXT_MIN_COVERAGE <= coverage <= WHITE_TEXT_MAX_COVERAGE):
        return None

    # OCR expects dark ink on a white background.
    flattened = np.where(white_mask, 0, 255).astype(np.uint8)
    return Image.fromarray(flattened, mode="L")


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

    white_letters = isolate_white_letters(cell_img)

    if white_letters is not None:
        # Color has already been reduced to a binary white-letter mask. Keep it
        # independent of the requested grayscale threshold used by OCR retries.
        bw = white_letters
    else:
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


def _get_cleaned_cell(cell_img, threshold, processing_cache=None):
    """Return one cleaned variant, sharing it within a cell recognition."""
    if processing_cache is None:
        return clean_cell_for_ocr(cell_img, threshold=threshold)

    key = ("cleaned", threshold)
    if key not in processing_cache:
        processing_cache[key] = clean_cell_for_ocr(cell_img, threshold=threshold)
    return processing_cache[key]


def looks_like_capital_i(cell_img, processing_cache=None):
    cleaned = _get_cleaned_cell(cell_img, 210, processing_cache)
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


def get_normalized_letter_mask(
    cell_img,
    threshold="otsu",
    dark_cutoff=80,
    size=64,
    processing_cache=None,
):
    cache_key = ("normalized_mask", threshold, dark_cutoff, size)
    if processing_cache is not None and cache_key in processing_cache:
        return processing_cache[cache_key]

    cleaned = _get_cleaned_cell(cell_img, threshold, processing_cache)
    arr = np.array(cleaned)

    dark = arr < dark_cutoff
    ys, xs = np.where(dark)

    if len(xs) == 0 or len(ys) == 0:
        result = (None, None)
        if processing_cache is not None:
            processing_cache[cache_key] = result
        return result

    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()

    width = x_max - x_min + 1
    height = y_max - y_min + 1

    if width <= 0 or height <= 0:
        result = (None, None)
        if processing_cache is not None:
            processing_cache[cache_key] = result
        return result

    letter = dark[y_min:y_max + 1, x_min:x_max + 1]
    letter_img = Image.fromarray((letter * 255).astype(np.uint8))
    letter_img = letter_img.resize((size, size))

    result = (np.array(letter_img) > 0, width / height)
    if processing_cache is not None:
        processing_cache[cache_key] = result
    return result


def looks_like_capital_p(cell_img, processing_cache=None):
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
        dark_cutoff=80,
        processing_cache=processing_cache,
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
    right_bowl_bridge = letter[12:25, 50:64]

    lower_right = letter[42:64, 28:64]
    lower_left = letter[42:64, 0:22]

    far_right_upper = letter[10:34, 50:64]
    far_right_lower = letter[42:64, 50:64]

    left_stem_density = left_stem.mean()
    top_bar_density = top_bar.mean()
    middle_bar_density = middle_bar.mean()
    upper_right_bowl_density = upper_right_bowl.mean()
    mid_right_bowl_density = mid_right_bowl.mean()
    right_bowl_bridge_density = right_bowl_bridge.mean()
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
        # Unlike F's two disconnected horizontal bars, P has a right-side
        # curve joining its top and middle strokes.
        and right_bowl_bridge_density > 0.08
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


def looks_like_capital_f(cell_img, processing_cache=None):
    """
    Backup detector for capital F.
    It is intentionally conservative: strong left stem and top/middle bars,
    but no P-style upper-right bowl or lower-right stroke.
    """
    letter, aspect_ratio = get_normalized_letter_mask(
        cell_img,
        threshold=190,
        dark_cutoff=80,
        processing_cache=processing_cache,
    )

    if letter is None:
        return False

    if aspect_ratio < 0.25:
        return False

    left_stem = letter[:, 0:16]
    top_bar = letter[0:18, 0:52]
    middle_bar = letter[24:42, 0:52]
    lower_left = letter[42:64, 0:22]

    right_bowl_bridge = letter[12:25, 50:64]
    far_right_lower = letter[42:64, 50:64]
    lower_right = letter[42:64, 28:64]
    bottom_right_bar = letter[48:64, 24:56]

    left_stem_density = left_stem.mean()
    top_bar_density = top_bar.mean()
    middle_bar_density = middle_bar.mean()
    lower_left_density = lower_left.mean()
    right_bowl_bridge_density = right_bowl_bridge.mean()
    far_right_lower_density = far_right_lower.mean()
    lower_right_density = lower_right.mean()
    bottom_right_bar_density = bottom_right_bar.mean()

    has_left_stem = left_stem_density > 0.22
    has_top_bar = top_bar_density > 0.12
    has_middle_bar = middle_bar_density > 0.10
    has_lower_stem = lower_left_density > 0.08

    no_upper_bowl = right_bowl_bridge_density < 0.05
    lower_right_clear = lower_right_density < 0.08
    far_right_lower_clear = far_right_lower_density < 0.05
    no_bottom_bar = bottom_right_bar_density < 0.05

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


def looks_like_capital_o(cell_img, processing_cache=None):
    letter, aspect_ratio = get_normalized_letter_mask(
        cell_img,
        threshold="otsu",
        dark_cutoff=90,
        processing_cache=processing_cache,
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

    no_heavy_tail = tail_density < 0.75

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


def looks_like_capital_o_misread_as_t(cell_img, processing_cache=None):
    """Distinguish a chunky O from Tesseract's occasional T prediction.

    The normal O detector is deliberately strict because it is also used for
    unknown, C, D, and Q predictions.  In the much narrower T-vs-O case we can
    accept the game's squarer O: its empty center and continuous strokes on all
    four sides are features a real capital T cannot have.
    """
    letter, aspect_ratio = get_normalized_letter_mask(
        cell_img,
        threshold="otsu",
        dark_cutoff=90,
        processing_cache=processing_cache,
    )

    if letter is None or not (0.70 <= aspect_ratio <= 1.30):
        return False

    left_density = letter[:, 0:16].mean()
    right_density = letter[:, 48:64].mean()
    top_density = letter[0:16, :].mean()
    bottom_density = letter[48:64, :].mean()
    center_density = letter[22:42, 22:42].mean()
    lower_right_density = letter[42:64, 42:64].mean()

    corner_densities = [
        letter[0:16, 0:16].mean(),
        letter[0:16, 48:64].mean(),
        letter[48:64, 0:16].mean(),
        letter[48:64, 48:64].mean()
    ]

    return (
        left_density > 0.10
        and right_density > 0.10
        and top_density > 0.08
        and bottom_density > 0.08
        and center_density < 0.12
        and lower_right_density < 0.85
        and abs(left_density - right_density) < 0.20
        and abs(top_density - bottom_density) < 0.20
        and max(corner_densities) < 0.72
    )


def correct_t_o_confusion(
    cell_img,
    letter,
    confidence,
    processing_cache=None,
):
    """Override a T result only when the pixels clearly form a closed O."""
    if letter == "T" and looks_like_capital_o_misread_as_t(
        cell_img,
        processing_cache=processing_cache,
    ):
        return "O", 96

    return letter, confidence



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


def _build_backup_template_index(templates):
    """Pack templates into one matrix for a vectorized similarity pass."""
    if not templates:
        return None

    letters = tuple(dict.fromkeys(letter for letter, _, _ in templates))
    letter_numbers = {letter: index for index, letter in enumerate(letters)}
    template_letter_numbers = np.fromiter(
        (letter_numbers[letter] for letter, _, _ in templates),
        dtype=np.intp,
        count=len(templates),
    )
    matrix = np.ascontiguousarray(
        np.stack([template.ravel() for _, template, _ in templates]),
        dtype=np.float32,
    )
    paths = tuple(path for _, _, path in templates)
    template_indexes_by_letter = tuple(
        np.flatnonzero(template_letter_numbers == number)
        for number in range(len(letters))
    )

    return (
        letters,
        matrix,
        template_letter_numbers,
        paths,
        template_indexes_by_letter,
    )


@lru_cache(maxsize=1)
def load_backup_template_index():
    """Load and vectorize saved templates once for all cells and scans."""
    return _build_backup_template_index(load_backup_letter_templates())


def match_cell_with_backup_images(cell_img, processing_cache=None):
    """
    Try to identify a cell by comparing it to saved backup/template images.

    Returns:
        (letter, confidence, score, template_path)

    If no good match is found:
        ("?", -1, -1, None)
    """
    match_cache_key = ("backup_match",)
    if processing_cache is not None and match_cache_key in processing_cache:
        return processing_cache[match_cache_key]

    def remember(result):
        if processing_cache is not None:
            processing_cache[match_cache_key] = result
        return result

    template_index = load_backup_template_index()
    if template_index is None:
        return remember(("?", -1, -1, None))

    (
        letters,
        template_matrix,
        template_letter_numbers,
        paths,
        template_indexes_by_letter,
    ) = template_index

    # Use the same cleaned OCR image so matching sees the letter, not the tile.
    query_cache_key = ("backup_query",)
    if processing_cache is not None and query_cache_key in processing_cache:
        query = processing_cache[query_cache_key]
    else:
        query_img = _get_cleaned_cell(cell_img, "otsu", processing_cache)
        query = normalize_image_for_backup_matching(query_img)
        if processing_cache is not None:
            processing_cache[query_cache_key] = query

    if query is None:
        return remember(("?", -1, -1, None))

    # All templates are normalized, so the dot product is cosine similarity.
    # einsum avoids one Python/NumPy call per saved image.
    scores = np.einsum(
        "ij,j->i",
        template_matrix,
        query.ravel(),
        optimize=False,
    )

    # Compare the best score for each *letter*. The previous implementation
    # could incorrectly treat a second template of the winning letter as the
    # runner-up, making a strong match appear ambiguous.
    scores_by_letter = np.full(len(letters), -np.inf, dtype=np.float32)
    np.maximum.at(scores_by_letter, template_letter_numbers, scores)

    best_letter_number = int(np.argmax(scores_by_letter))
    best_score = float(scores_by_letter[best_letter_number])
    if len(scores_by_letter) > 1:
        runner_up_scores = scores_by_letter.copy()
        runner_up_scores[best_letter_number] = -np.inf
        second_best_score = float(np.max(runner_up_scores))
    else:
        second_best_score = -1.0

    best_letter = letters[best_letter_number]
    matching_indexes = template_indexes_by_letter[best_letter_number]
    best_template_index = int(matching_indexes[np.argmax(scores[matching_indexes])])
    best_path = paths[best_template_index]

    margin = best_score - second_best_score

    if best_score >= BACKUP_MATCH_THRESHOLD and margin >= BACKUP_MATCH_MARGIN:
        # Convert a 0-1 similarity score into a confidence-like number.
        confidence = min(98, max(70, int(round(best_score * 100))))
        return remember((best_letter, confidence, best_score, best_path))

    return remember(("?", -1, best_score, best_path))


def maybe_use_backup_image_match(
    cell_img,
    letter,
    confidence,
    processing_cache=None,
):
    """
    Use template image matching after OCR only when OCR is unknown or low confidence.
    This preserves the old fallback behavior when template-first matching fails.
    """
    if not USE_BACKUP_IMAGE_MATCHING:
        return letter, confidence

    if letter != "?" and confidence >= BACKUP_MATCH_WHEN_CONF_BELOW:
        return letter, confidence

    backup_letter, backup_conf, backup_score, backup_path = match_cell_with_backup_images(
        cell_img,
        processing_cache=processing_cache,
    )

    if backup_letter != "?":
        print(
            f"Backup image match used after OCR: {backup_letter} "
            f"(score={backup_score:.3f}, conf={backup_conf}, "
            f"previous={letter}/{confidence:.1f})"
        )
        return backup_letter, backup_conf

    return letter, confidence


def maybe_use_template_image_match_first(cell_img, processing_cache=None):
    """
    Try template image matching before OCR.

    Returns:
        (letter, confidence) if a confident template match is found.
        (None, None) if OCR should run instead.
    """
    if not USE_BACKUP_IMAGE_MATCHING or not USE_TEMPLATE_MATCHING_FIRST:
        return None, None

    backup_letter, backup_conf, backup_score, backup_path = match_cell_with_backup_images(
        cell_img,
        processing_cache=processing_cache,
    )

    if backup_letter != "?" and backup_score >= TEMPLATE_FIRST_MATCH_THRESHOLD:
        print(
            f"Template image match used before OCR: {backup_letter} "
            f"(score={backup_score:.3f}, conf={backup_conf})"
        )
        return backup_letter, backup_conf

    return None, None

def ocr_attempt(cell_img, thresholds, psm_modes, cleaned_cache=None):
    best_letter = "?"
    best_conf = -1

    for threshold in thresholds:
        cleaned = _get_cleaned_cell(cell_img, threshold, cleaned_cache)

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


def _recognize_single_letter(cell_img, processing_cache=None):
    # Both OCR passes use the same Otsu cleanup in the default configuration.
    # Keep it per-cell so fallback PSM recognition does not repeat the costly
    # image-processing pipeline.
    if processing_cache is None:
        processing_cache = {}

    template_letter, template_conf = maybe_use_template_image_match_first(
        cell_img,
        processing_cache=processing_cache,
    )
    if template_letter is not None:
        return correct_t_o_confusion(
            cell_img,
            template_letter,
            template_conf,
            processing_cache=processing_cache,
        )

    if looks_like_capital_i(cell_img, processing_cache=processing_cache):
        return "I", 99

    letter, conf = ocr_attempt(
        cell_img,
        FAST_THRESHOLDS,
        PSM_MODES_FAST,
        cleaned_cache=processing_cache,
    )

    corrected_letter, corrected_conf = correct_t_o_confusion(
        cell_img,
        letter,
        conf,
        processing_cache=processing_cache,
    )
    if corrected_letter != letter:
        return corrected_letter, corrected_conf

    if letter in {"?", "D", "C", "Q"} or conf < 70:
        if looks_like_capital_o(cell_img, processing_cache=processing_cache):
            return "O", 96

    if letter != "?" and conf >= 65 and letter not in {"F", "R", "D", "B", "C", "Q"}:
        return letter, conf

    if letter in {"?", "P", "F", "R", "D", "B"} or conf < 65:
        looks_like_p = looks_like_capital_p(
            cell_img,
            processing_cache=processing_cache,
        )
        looks_like_f = looks_like_capital_f(
            cell_img,
            processing_cache=processing_cache,
        )

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
        PSM_MODES_FALLBACK,
        cleaned_cache=processing_cache,
    )

    corrected_letter2, corrected_conf2 = correct_t_o_confusion(
        cell_img,
        letter2,
        conf2,
        processing_cache=processing_cache,
    )
    if corrected_letter2 != letter2:
        return corrected_letter2, corrected_conf2

    if letter2 in {"?", "D", "C", "Q"} or conf2 < 70:
        if looks_like_capital_o(cell_img, processing_cache=processing_cache):
            return "O", 96

    if letter2 in {"?", "P", "F", "R", "D", "B"} or conf2 < 60:
        looks_like_p = looks_like_capital_p(
            cell_img,
            processing_cache=processing_cache,
        )
        looks_like_f = looks_like_capital_f(
            cell_img,
            processing_cache=processing_cache,
        )

        if looks_like_p:
            return "P", 96

        if looks_like_f:
            return "F", 94

        if letter2 == "P":
            return "F", min(conf2, 70)

    if conf2 > conf:
        return maybe_use_backup_image_match(
            cell_img,
            letter2,
            conf2,
            processing_cache=processing_cache,
        )

    return maybe_use_backup_image_match(
        cell_img,
        letter,
        conf,
        processing_cache=processing_cache,
    )


def clear_ocr_result_cache():
    """Discard cached cell results, primarily for tests and live reconfiguration."""
    with _OCR_RESULT_CACHE_LOCK:
        _OCR_RESULT_CACHE.clear()
    with _NORMALIZED_GLYPH_RESULT_CACHE_LOCK:
        _NORMALIZED_GLYPH_RESULT_CACHE.clear()


def _cell_image_cache_key(cell_img):
    if not isinstance(cell_img, Image.Image):
        return None

    digest = hashlib.blake2b(cell_img.tobytes(), digest_size=16).digest()
    return cell_img.mode, cell_img.size, digest


def _normalized_glyph_cache_key(cell_img, processing_cache):
    """Hash the color-independent cleaned glyph used by recognition."""
    if not isinstance(cell_img, Image.Image):
        return None

    cleaned = _get_cleaned_cell(cell_img, "otsu", processing_cache)
    packed_ink = np.packbits(np.asarray(cleaned) < 128)
    digest = hashlib.blake2b(packed_ink.tobytes(), digest_size=16).digest()
    return cleaned.size, digest


def _get_lru_result(cache, lock, key):
    if key is None:
        return None

    with lock:
        result = cache.pop(key, None)
        if result is not None:
            cache[key] = result
        return result


def _store_lru_result(cache, lock, key, result, max_size):
    if key is None or max_size <= 0:
        return

    with lock:
        cache[key] = result
        cache.move_to_end(key)
        while len(cache) > max_size:
            cache.popitem(last=False)


def ocr_single_letter(cell_img):
    """Recognize one cell, reusing recent exact and normalized results."""
    cache_key = _cell_image_cache_key(cell_img)
    if cache_key is None:
        return _recognize_single_letter(cell_img)

    if OCR_RESULT_CACHE_SIZE > 0:
        cached = _get_lru_result(
            _OCR_RESULT_CACHE,
            _OCR_RESULT_CACHE_LOCK,
            cache_key,
        )
        if cached is not None:
            return cached

    processing_cache = {}
    glyph_key = None
    if NORMALIZED_GLYPH_CACHE_SIZE > 0:
        glyph_key = _normalized_glyph_cache_key(cell_img, processing_cache)
        glyph_cached = _get_lru_result(
            _NORMALIZED_GLYPH_RESULT_CACHE,
            _NORMALIZED_GLYPH_RESULT_CACHE_LOCK,
            glyph_key,
        )
        if glyph_cached is not None:
            _store_lru_result(
                _OCR_RESULT_CACHE,
                _OCR_RESULT_CACHE_LOCK,
                cache_key,
                glyph_cached,
                OCR_RESULT_CACHE_SIZE,
            )
            return glyph_cached

    result = _recognize_single_letter(
        cell_img,
        processing_cache=processing_cache,
    )

    _store_lru_result(
        _NORMALIZED_GLYPH_RESULT_CACHE,
        _NORMALIZED_GLYPH_RESULT_CACHE_LOCK,
        glyph_key,
        result,
        NORMALIZED_GLYPH_CACHE_SIZE,
    )
    _store_lru_result(
        _OCR_RESULT_CACHE,
        _OCR_RESULT_CACHE_LOCK,
        cache_key,
        result,
        OCR_RESULT_CACHE_SIZE,
    )

    return result


def get_ocr_executor():
    """Return the process-wide worker pool used for cell recognition."""
    global _OCR_EXECUTOR

    with _OCR_EXECUTOR_LOCK:
        if _OCR_EXECUTOR is None:
            _OCR_EXECUTOR = ThreadPoolExecutor(max_workers=OCR_MAX_WORKERS)
        return _OCR_EXECUTOR


def close_ocr_executor():
    """Shut down the reusable OCR worker pool."""
    global _OCR_EXECUTOR

    with _OCR_EXECUTOR_LOCK:
        executor = _OCR_EXECUTOR
        _OCR_EXECUTOR = None

    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


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
    dark_grid_centers = detect_dark_grid_centers(img)
    if dark_grid_centers is not None:
        x_centers, y_centers = dark_grid_centers
        cell_w = float(np.median(np.diff(x_centers)))
        cell_h = float(np.median(np.diff(y_centers)))
        cells = []
        cell_centers_screen = {}
        screen_left = capture_region["left"] if capture_region else 0
        screen_top = capture_region["top"] if capture_region else 0

        for row, center_y in enumerate(y_centers):
            for col, center_x in enumerate(x_centers):
                left = max(0, int(round(center_x - cell_w / 2)))
                top = max(0, int(round(center_y - cell_h / 2)))
                right = min(img.width, int(round(center_x + cell_w / 2)))
                bottom = min(img.height, int(round(center_y + cell_h / 2)))

                cells.append((row, col, img.crop((left, top, right, bottom))))
                cell_centers_screen[(row, col)] = (
                    int(round(screen_left + center_x)),
                    int(round(screen_top + center_y)),
                )

        return cells, cell_centers_screen

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

    executor = get_ocr_executor()
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
    results = []

    for line in WORD_LINES:
        word = "".join(grid[row][col] for row, col in line)
        if "?" in word:
            continue

        if is_english_word(word):
            positions = list(line)
            results.append({
                "word": word,
                "start": positions[0],
                "end": positions[-1],
                "positions": positions
            })

        reverse_word = word[::-1]
        if is_english_word(reverse_word):
            reverse_positions = list(reversed(line))
            results.append({
                "word": reverse_word,
                "start": reverse_positions[0],
                "end": reverse_positions[-1],
                "positions": reverse_positions
            })

    # Each oriented path is generated exactly once, so no de-duplication pass
    # is necessary.
    return results


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

    # Keep one uninterrupted press across the entire word. pyautogui.dragTo()
    # manages its own press/release cycle, so calling it once per letter can
    # split a five-letter word into four separate gestures.
    pyautogui.mouseDown(button="left")
    try:
        for x, y in points[1:]:
            pyautogui.moveTo(
                x,
                y,
                duration=TRACE_DRAG_DURATION
            )
    finally:
        # Always release the mouse if a move raises or the failsafe triggers.
        pyautogui.mouseUp(button="left")

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
    # Disk loading and normalization happen while the user aligns the guide,
    # keeping template startup work off the first scan's critical path.
    with ThreadPoolExecutor(max_workers=1) as template_loader:
        if USE_BACKUP_IMAGE_MATCHING:
            template_loader.submit(load_backup_template_index)
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
    finally:
        close_screen_capture()
        close_ocr_executor()


if __name__ == "__main__":
    run_auto_capture()
