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

GRID_SIZE = 4
AUTO_CAPTURE_SECONDS = 1
PAUSE_POPUP_EVERY_SECONDS = 10
PAUSE_POPUP_ENABLED = True

CAPTURE_REGION = {
    "left": 200,
    "top": 150,
    "width": 370,
    "height": 370
}

GUIDE_INITIAL_SIZE = CAPTURE_REGION["width"]
GUIDE_SIZE_STEP = 25
GUIDE_MIN_SIZE = 250
GUIDE_MAX_SIZE = 1000

FAST_THRESHOLDS = ["otsu", 190]
FALLBACK_THRESHOLDS = ["otsu", 160, 210]
PSM_MODES_FAST = [10]
PSM_MODES_FALLBACK = [10, 13]

ASK_FOR_UNKNOWN_CELLS = False
SAVE_FAILED_CELLS = False
USE_MANUAL_GRID_PICKER = True
MANUAL_GRID_RUN_ONCE = True
MANUAL_GRID_PREFILL_WITH_OCR = False

SAVE_TEMPLATE_IMAGES = True
TEMPLATE_IMAGE_ROOT = "ocr_letter_templates_wordBlitz"

USE_BACKUP_IMAGE_MATCHING = True
USE_TEMPLATE_MATCHING_FIRST = True
BACKUP_IMAGE_ROOT = os.path.join(TEMPLATE_IMAGE_ROOT, "high_confidence")
BACKUP_MATCH_WHEN_CONF_BELOW = 65
BACKUP_MATCH_THRESHOLD = 0.72
TEMPLATE_FIRST_MATCH_THRESHOLD = 0.78
BACKUP_MATCH_MARGIN = 0.035
BACKUP_MAX_TEMPLATES_PER_LETTER = 80
BACKUP_MATCH_IMAGE_SIZE = 96

MIN_WORD_LENGTH = 2
MAX_WORD_LENGTH = GRID_SIZE * GRID_SIZE
WORD_FREQ_THRESHOLD = 2.3
WORD_LIST_SIZE = 120000
WORD_LIST_PATH = None
STRICT_REAL_ENGLISH_WORDS_ONLY = True
REQUIRE_WORD_IN_DICTIONARY = True

COMMON_TWO_LETTER_WORDS = {
    "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF",
    "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OH", "ON", "OR",
    "OX", "SO", "TO", "UP", "US", "WE",
}

CUSTOM_ALLOWED_WORDS = set()

CUSTOM_BLOCKED_WORDS = {
    "AA", "AB", "AD", "AE", "AG", "AH", "AI", "AL", "AR", "AW", "AX",
    "AY", "BA", "BI", "BO", "DA", "DE", "DI", "ED", "EF", "EH", "EL",
    "EM", "EN", "ER", "ES", "ET", "EX", "FA", "FE", "GI", "HA", "HI",
    "HM", "HO", "ID", "JO", "KA", "KI", "KO", "LA", "LI", "LO", "MA",
    "MI", "MM", "MO", "MU", "NA", "NE", "NU", "OD", "OE", "OI", "OM",
    "OP", "OS", "OW", "PA", "PE", "PI", "QI", "RE", "SH", "SI", "TA",
    "TE", "TI", "UH", "UM", "UN", "UT", "WO", "XI", "XU", "YA", "YE", "YO", "ZA",
}

IGNORE_TILE_UI_NUMBERS_AND_BADGES = True
LETTER_CROP_LEFT_RATIO = 0.08
LETTER_CROP_RIGHT_RATIO = 0.08
LETTER_CROP_TOP_RATIO = 0.30
LETTER_CROP_BOTTOM_RATIO = 0.08

ALLOW_DUPLICATE_WORDS_WITH_DIFFERENT_PATHS = False
ORDER_WORDS_TO_MINIMIZE_MOUSE_TRAVEL = False

HIGH_CONFIDENCE_THRESHOLD = 80
LOW_CONFIDENCE_THRESHOLD = 80
SAVE_CLEANED_TEMPLATE_IMAGE = True
SAVE_ORIGINAL_TEMPLATE_IMAGE = False

AUTO_TRACE_FOUND_WORD = True
TRACE_ONLY_FIRST_WORD = False
MAX_WORDS_TO_TRACE = 80

TRACE_MOVE_DURATION = 0.014
TRACE_DRAG_DURATION = 0.042
TRACE_PAUSE_AFTER_WORD = 0.003
TRACE_STEP_PIXELS = 40
TRACE_STEP_DURATION = 0.0015
TRACE_START_HOLD_SECONDS = 0.028
TRACE_TILE_HOLD_SECONDS = 0.002
TRACE_END_HOLD_SECONDS = 0.021
TRACE_MOUSE_BUTTON = "left"
TRACE_CLICK_OFFSET_X = 0
TRACE_CLICK_OFFSET_Y = 0

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.02

OCR_CONFIG_TEMPLATE = (
    "--psm {psm} "
    "--oem 3 "
    "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0 "
    "-c load_system_dawg=0 "
    "-c load_freq_dawg=0 "
    "-c classify_bln_numeric_mode=0"
)


def normalize_candidate_word(word):
    return re.sub(r"[^A-Z]", "", word.strip().upper())


def is_real_word_shape(word):
    word = normalize_candidate_word(word)
    if len(word) < MIN_WORD_LENGTH or len(word) > MAX_WORD_LENGTH:
        return False
    return bool(re.fullmatch(r"[A-Z]+", word))


def should_keep_dictionary_word(word):
    word = normalize_candidate_word(word)
    if not is_real_word_shape(word):
        return False
    if word in CUSTOM_BLOCKED_WORDS:
        return False
    if word in CUSTOM_ALLOWED_WORDS:
        return True
    if len(word) == 2:
        return word in COMMON_TWO_LETTER_WORDS
    if not STRICT_REAL_ENGLISH_WORDS_ONLY:
        return True
    return zipf_frequency(word.lower(), "en") >= WORD_FREQ_THRESHOLD


@lru_cache(maxsize=1)
def load_word_sets():
    words = set()
    if WORD_LIST_PATH and os.path.exists(WORD_LIST_PATH):
        with open(WORD_LIST_PATH, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                word = normalize_candidate_word(line)
                if should_keep_dictionary_word(word):
                    words.add(word)
    if not words:
        try:
            from wordfreq import top_n_list
            for raw_word in top_n_list("en", WORD_LIST_SIZE):
                word = normalize_candidate_word(raw_word)
                if should_keep_dictionary_word(word):
                    words.add(word)
        except Exception:
            words = set()
    for raw_word in CUSTOM_ALLOWED_WORDS:
        word = normalize_candidate_word(raw_word)
        if is_real_word_shape(word):
            words.add(word)
    prefixes = set()
    for word in words:
        for i in range(1, len(word) + 1):
            prefixes.add(word[:i])
    return words, prefixes


def is_english_word(word):
    word = normalize_candidate_word(word)
    if not is_real_word_shape(word):
        return False
    words, _ = load_word_sets()
    if REQUIRE_WORD_IN_DICTIONARY:
        return word in words
    if words and word in words:
        return True
    return should_keep_dictionary_word(word)


def is_possible_word_prefix(prefix):
    prefix = normalize_candidate_word(prefix)
    if not prefix:
        return True
    if not re.fullmatch(r"[A-Z]+", prefix):
        return False
    _, prefixes = load_word_sets()
    if prefixes:
        return prefix in prefixes
    return True


def show_capture_guide(region):
    root = tk.Tk()
    root.title(f"Capture Guide — {GRID_SIZE}x{GRID_SIZE} Grid")
    start_size = int(region.get("width", GUIDE_INITIAL_SIZE))
    start_size = max(GUIDE_MIN_SIZE, min(GUIDE_MAX_SIZE, start_size))
    guide = {
        "size": start_size,
        "left": int(region.get("left", CAPTURE_REGION["left"])),
        "top": int(region.get("top", CAPTURE_REGION["top"])),
    }
    root.geometry(f"{guide['size']}x{guide['size']}+{guide['left']}+{guide['top']}")
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.90)
    root.resizable(False, False)
    final_region = region.copy()
    final_region["width"] = guide["size"]
    final_region["height"] = guide["size"]
    cancelled = {"value": False}
    drag_data = {"x": 0, "y": 0}
    canvas = tk.Canvas(root, width=guide["size"], height=guide["size"], highlightthickness=0, bg="#eeeeee")
    canvas.pack(fill="both", expand=True)
    button_frame = tk.Frame(root, bg="white")
    size_var = tk.StringVar(value=str(guide["size"]))
    grid_line_widgets = []

    def sync_final_region():
        final_region["left"] = canvas.winfo_rootx()
        final_region["top"] = canvas.winfo_rooty()
        final_region["width"] = guide["size"]
        final_region["height"] = guide["size"]

    def redraw_guide():
        size = guide["size"]
        canvas.config(width=size, height=size)
        canvas.delete("all")
        grid_color = "#000000"
        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                left = round(size * col / GRID_SIZE)
                top = round(size * row / GRID_SIZE)
                right = round(size * (col + 1) / GRID_SIZE)
                bottom = round(size * (row + 1) / GRID_SIZE)
                canvas.create_rectangle(
                    left,
                    top,
                    right,
                    bottom,
                    outline=grid_color,
                    width=5,
                )
        canvas.create_rectangle(3, 3, size - 3, size - 3, outline=grid_color, width=6)
        canvas.create_text(size // 2, 28, text="Drag to align. Edit size or use +/-. Start when ready.", fill="black", font=("Arial", 14, "bold"))
        canvas.create_text(size // 2, 56, text=f"Guide size: {size} x {size}", fill="black", font=("Arial", 11))

        for widget in grid_line_widgets:
            widget.destroy()
        grid_line_widgets.clear()

        line_width = 5
        for index in range(1, GRID_SIZE):
            offset = round(size * index / GRID_SIZE)
            vertical_line = tk.Frame(canvas, bg=grid_color, borderwidth=0, highlightthickness=0)
            vertical_line.place(x=offset - line_width // 2, y=0, width=line_width, height=size)
            vertical_line.lift()
            horizontal_line = tk.Frame(canvas, bg=grid_color, borderwidth=0, highlightthickness=0)
            horizontal_line.place(x=0, y=offset - line_width // 2, width=size, height=line_width)
            horizontal_line.lift()
            grid_line_widgets.extend((vertical_line, horizontal_line))

        border_width = 6
        border_specs = (
            {"x": 0, "y": 0, "width": size, "height": border_width},
            {"x": 0, "y": size - border_width, "width": size, "height": border_width},
            {"x": 0, "y": 0, "width": border_width, "height": size},
            {"x": size - border_width, "y": 0, "width": border_width, "height": size},
        )
        for spec in border_specs:
            border = tk.Frame(canvas, bg=grid_color, borderwidth=0, highlightthickness=0)
            border.place(**spec)
            border.lift()
            grid_line_widgets.append(border)

        button_frame.update_idletasks()
        frame_x = max((size - button_frame.winfo_reqwidth()) // 2, 5)
        button_frame.place(x=frame_x, y=max(size - 45, 5))
        button_frame.lift()
        sync_final_region()

    def resize_guide(delta):
        old_size = guide["size"]
        new_size = max(GUIDE_MIN_SIZE, min(GUIDE_MAX_SIZE, old_size + delta))
        if new_size == old_size:
            return
        current_left = canvas.winfo_rootx()
        current_top = canvas.winfo_rooty()
        window_offset_x = current_left - root.winfo_x()
        window_offset_y = current_top - root.winfo_y()
        center_x = current_left + old_size // 2
        center_y = current_top + old_size // 2
        guide["size"] = new_size
        size_var.set(str(new_size))
        new_left = int(center_x - new_size // 2 - window_offset_x)
        new_top = int(center_y - new_size // 2 - window_offset_y)
        root.geometry(f"{new_size}x{new_size}+{new_left}+{new_top}")
        redraw_guide()

    def apply_size_entry():
        try:
            requested_size = int(size_var.get())
        except ValueError:
            size_var.set(str(guide["size"]))
            return
        requested_size = max(GUIDE_MIN_SIZE, min(GUIDE_MAX_SIZE, requested_size))
        size_var.set(str(requested_size))
        resize_guide(requested_size - guide["size"])

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

    root.protocol("WM_DELETE_WINDOW", cancel)

    decrease_button = tk.Button(button_frame, text="−", command=lambda: resize_guide(-GUIDE_SIZE_STEP), font=("Arial", 12, "bold"), width=3)
    decrease_button.pack(side="left", padx=3)
    increase_button = tk.Button(button_frame, text="+", command=lambda: resize_guide(GUIDE_SIZE_STEP), font=("Arial", 12, "bold"), width=3)
    increase_button.pack(side="left", padx=3)
    tk.Label(button_frame, text="Size:", bg="white", font=("Arial", 11)).pack(side="left", padx=(7, 2))
    size_input = tk.Spinbox(
        button_frame,
        from_=GUIDE_MIN_SIZE,
        to=GUIDE_MAX_SIZE,
        increment=GUIDE_SIZE_STEP,
        textvariable=size_var,
        command=apply_size_entry,
        width=5,
        font=("Arial", 11),
    )
    size_input.pack(side="left", padx=(0, 5))
    size_input.bind("<Return>", lambda event: (apply_size_entry(), "break")[1])
    tk.Button(button_frame, text="Start", command=confirm, font=("Arial", 12, "bold")).pack(side="left", padx=5)
    tk.Button(button_frame, text="Cancel", command=cancel, font=("Arial", 12)).pack(side="left", padx=5)

    def start_drag(event):
        root.focus_force()
        drag_data["x"] = event.x
        drag_data["y"] = event.y

    def drag_window(event):
        x = root.winfo_x() + event.x - drag_data["x"]
        y = root.winfo_y() + event.y - drag_data["y"]
        root.geometry(f"+{x}+{y}")
        sync_final_region()

    def on_key(event):
        if event.keysym in {"plus", "equal", "KP_Add", "bracketright"}:
            resize_guide(GUIDE_SIZE_STEP)
        elif event.keysym in {"minus", "underscore", "KP_Subtract", "bracketleft"}:
            resize_guide(-GUIDE_SIZE_STEP)
        elif event.keysym in {"Return", "space"}:
            confirm()
        elif event.keysym == "Escape":
            cancel()

    def on_mouse_wheel(event):
        if getattr(event, "delta", 0) > 0:
            resize_guide(GUIDE_SIZE_STEP)
        elif getattr(event, "delta", 0) < 0:
            resize_guide(-GUIDE_SIZE_STEP)

    def invoke_resize_button(button):
        button.invoke()
        return "break"

    canvas.bind("<ButtonPress-1>", start_drag)
    canvas.bind("<B1-Motion>", drag_window)
    root.bind("<Key>", on_key)
    root.bind("<MouseWheel>", on_mouse_wheel)
    root.bind("<Button-4>", lambda event: resize_guide(GUIDE_SIZE_STEP))
    root.bind("<Button-5>", lambda event: resize_guide(-GUIDE_SIZE_STEP))
    root.bind("<Command-KeyPress-equal>", lambda event: invoke_resize_button(increase_button))
    root.bind("<Command-KeyPress-plus>", lambda event: invoke_resize_button(increase_button))
    root.bind("<Command-KeyPress-minus>", lambda event: invoke_resize_button(decrease_button))
    root.bind("<Command-KeyPress-KP_Add>", lambda event: invoke_resize_button(increase_button))
    root.bind("<Command-KeyPress-KP_Subtract>", lambda event: invoke_resize_button(decrease_button))
    redraw_guide()
    root.update_idletasks()
    window_offset_x = canvas.winfo_rootx() - root.winfo_x()
    window_offset_y = canvas.winfo_rooty() - root.winfo_y()
    root.geometry(
        f"{guide['size']}x{guide['size']}"
        f"+{guide['left'] - window_offset_x}+{guide['top'] - window_offset_y}"
    )

    def focus_guide():
        root.lift()
        root.focus_set()
        root.focus_force()

    root.after(100, focus_guide)
    root.after(200, redraw_guide)
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
    red_like = (r > 150) & (g < 150) & (b < 180) & (r > g * 1.2) & (r > b * 1.2)
    arr[red_like] = [255, 255, 255]
    return Image.fromarray(arr)


def capture_screen_region(region):
    with mss.mss() as sct:
        screenshot = sct.grab(region)
        img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
    return remove_red_crosshairs(img)


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
    return img.crop((left, top, right, bottom)), left, top


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
    square.paste(img, ((size - w) // 2, (size - h) // 2))
    return square


def crop_out_tile_ui(cell_img):
    if not IGNORE_TILE_UI_NUMBERS_AND_BADGES:
        return cell_img
    w, h = cell_img.size
    left = int(w * LETTER_CROP_LEFT_RATIO)
    right = int(w * (1.0 - LETTER_CROP_RIGHT_RATIO))
    top = int(h * LETTER_CROP_TOP_RATIO)
    bottom = int(h * (1.0 - LETTER_CROP_BOTTOM_RATIO))
    if right <= left or bottom <= top:
        return cell_img
    return cell_img.crop((left, top, right, bottom))


def clean_cell_for_ocr(cell_img, threshold="otsu"):
    cell_img = crop_out_tile_ui(cell_img)
    w, h = cell_img.size
    margin_x = int(w * 0.04)
    margin_y = int(h * 0.04)
    cell_img = cell_img.crop((margin_x, margin_y, w - margin_x, h - margin_y))
    gray = ImageOps.grayscale(cell_img)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(3.5)
    gray = gray.filter(ImageFilter.SHARPEN)
    threshold_value = otsu_threshold(gray) if threshold == "otsu" else int(threshold)
    bw = gray.point(lambda p: 0 if p < threshold_value else 255)
    bw = bw.filter(ImageFilter.MedianFilter(size=3))
    bw = crop_to_letter_bounds(bw)
    bw = pad_to_square(bw)
    return bw.resize((240, 240), Image.Resampling.LANCZOS)


def looks_like_capital_i(cell_img):
    arr = np.array(clean_cell_for_ocr(cell_img, threshold=210))
    dark = arr < 80
    ys, xs = np.where(dark)
    if len(xs) == 0 or len(ys) == 0:
        return False
    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()
    letter_width = x_max - x_min + 1
    letter_height = y_max - y_min + 1
    img_h, img_w = arr.shape
    center_x = (x_min + x_max) / 2
    return letter_height > img_h * 0.35 and letter_width < img_w * 0.20 and img_w * 0.35 < center_x < img_w * 0.65 and len(xs) > 60


def looks_like_capital_p(cell_img):
    arr = np.array(clean_cell_for_ocr(cell_img, threshold=190))
    dark = arr < 80
    ys, xs = np.where(dark)
    if len(xs) == 0 or len(ys) == 0:
        return False
    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()
    width = x_max - x_min + 1
    height = y_max - y_min + 1
    if width <= 0 or height <= 0 or width / height < 0.35:
        return False
    letter = dark[y_min:y_max + 1, x_min:x_max + 1]
    letter_img = Image.fromarray((letter * 255).astype(np.uint8)).resize((64, 64))
    letter = np.array(letter_img) > 0
    left_stem = letter[:, 0:16]
    top_bar = letter[0:18, 0:52]
    middle_bar = letter[24:42, 0:52]
    upper_right_bowl = letter[8:34, 34:64]
    mid_right_bowl = letter[18:42, 34:64]
    lower_right = letter[42:64, 28:64]
    lower_left = letter[42:64, 0:22]
    far_right_upper = letter[10:34, 50:64]
    far_right_lower = letter[42:64, 50:64]
    return (
        left_stem.mean() > 0.22
        and top_bar.mean() > 0.12
        and middle_bar.mean() > 0.10
        and upper_right_bowl.mean() > 0.08
        and mid_right_bowl.mean() > 0.06
        and far_right_upper.mean() > 0.05
        and lower_right.mean() < 0.08
        and far_right_lower.mean() < 0.05
        and lower_left.mean() > 0.08
    )


def looks_like_capital_o(cell_img):
    arr = np.array(clean_cell_for_ocr(cell_img, threshold="otsu"))
    dark = arr < 90
    ys, xs = np.where(dark)
    if len(xs) == 0 or len(ys) == 0:
        return False
    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()
    width = x_max - x_min + 1
    height = y_max - y_min + 1
    if width <= 0 or height <= 0 or not 0.70 <= width / height <= 1.30:
        return False
    letter = dark[y_min:y_max + 1, x_min:x_max + 1]
    letter_img = Image.fromarray((letter * 255).astype(np.uint8)).resize((64, 64))
    letter = np.array(letter_img) > 0
    left_band = letter[:, 0:16]
    right_band = letter[:, 48:64]
    top_band = letter[0:16, :]
    bottom_band = letter[48:64, :]
    center = letter[22:42, 22:42]
    lower_right_tail = letter[42:64, 42:64]
    return (
        left_band.mean() > 0.10
        and right_band.mean() > 0.10
        and top_band.mean() > 0.08
        and bottom_band.mean() > 0.08
        and center.mean() < 0.12
        and lower_right_tail.mean() < 0.45
        and abs(left_band.mean() - right_band.mean()) < 0.20
        and abs(top_band.mean() - bottom_band.mean()) < 0.20
    )


def normalize_image_for_backup_matching(img):
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray)
    threshold_value = otsu_threshold(gray)
    bw = gray.point(lambda p: 0 if p < threshold_value else 255)
    bw = bw.filter(ImageFilter.MedianFilter(size=3))
    bw = crop_to_letter_bounds(bw)
    bw = pad_to_square(bw)
    bw = bw.resize((BACKUP_MATCH_IMAGE_SIZE, BACKUP_MATCH_IMAGE_SIZE), Image.Resampling.LANCZOS)
    arr = np.array(bw).astype(np.float32)
    ink = 1.0 - (arr / 255.0)
    if ink.sum() < 10:
        return None
    norm = np.linalg.norm(ink)
    if norm == 0:
        return None
    return ink / norm


def backup_template_sort_key(path):
    name = os.path.basename(path).lower()
    cleaned_bonus = 0 if "_cleaned_" in name else 1
    low_conf_penalty = 1 if "low_confidence" in path.lower() else 0
    return low_conf_penalty, cleaned_bonus, name


@lru_cache(maxsize=1)
def load_backup_letter_templates():
    templates = []
    if not USE_BACKUP_IMAGE_MATCHING or not os.path.isdir(BACKUP_IMAGE_ROOT):
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
        image_paths = sorted(image_paths, key=backup_template_sort_key)[:BACKUP_MAX_TEMPLATES_PER_LETTER]
        for path in image_paths:
            try:
                template_img = Image.open(path)
                normalized = normalize_image_for_backup_matching(template_img)
                if normalized is not None:
                    templates.append((letter, normalized, path))
            except Exception:
                continue
    return templates


def match_cell_with_backup_images(cell_img):
    templates = load_backup_letter_templates()
    if not templates:
        return "?", -1, -1, None
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
        confidence = min(98, max(70, int(round(best_score * 100))))
        return best_letter, confidence, best_score, best_path
    return "?", -1, best_score, best_path


def maybe_use_backup_image_match(cell_img, letter, confidence):
    if not USE_BACKUP_IMAGE_MATCHING:
        return letter, confidence
    if letter != "?" and confidence >= BACKUP_MATCH_WHEN_CONF_BELOW:
        return letter, confidence
    backup_letter, backup_conf, backup_score, _ = match_cell_with_backup_images(cell_img)
    if backup_letter != "?":
        print(f"Backup image match used after OCR: {backup_letter} (score={backup_score:.3f}, conf={backup_conf}, previous={letter}/{confidence:.1f})")
        return backup_letter, backup_conf
    return letter, confidence


def maybe_use_template_image_match_first(cell_img):
    if not USE_BACKUP_IMAGE_MATCHING or not USE_TEMPLATE_MATCHING_FIRST:
        return None, None
    backup_letter, backup_conf, backup_score, _ = match_cell_with_backup_images(cell_img)
    if backup_letter != "?" and backup_score >= TEMPLATE_FIRST_MATCH_THRESHOLD:
        print(f"Template image match used before OCR: {backup_letter} (score={backup_score:.3f}, conf={backup_conf})")
        return backup_letter, backup_conf
    return None, None


def ocr_attempt(cell_img, thresholds, psm_modes):
    best_letter = "?"
    best_conf = -1
    for threshold in thresholds:
        cleaned = clean_cell_for_ocr(cell_img, threshold=threshold)
        for psm in psm_modes:
            config = OCR_CONFIG_TEMPLATE.format(psm=psm)
            data = pytesseract.image_to_data(cleaned, config=config, output_type=pytesseract.Output.DICT)
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
    letter, conf = ocr_attempt(cell_img, FAST_THRESHOLDS, PSM_MODES_FAST)
    if letter in {"?", "D", "C", "Q"} or conf < 70:
        if looks_like_capital_o(cell_img):
            return "O", 96
    if letter != "?" and conf >= 65 and letter not in {"F", "R", "D", "B", "C", "Q"}:
        return letter, conf
    if letter in {"?", "P", "F", "R", "D", "B"} or conf < 65:
        if looks_like_capital_p(cell_img):
            return "P", 96
        if letter == "P" and not looks_like_capital_p(cell_img):
            return "F", min(conf, 70)
    letter2, conf2 = ocr_attempt(cell_img, FALLBACK_THRESHOLDS, PSM_MODES_FALLBACK)
    if letter2 in {"?", "D", "C", "Q"} or conf2 < 70:
        if looks_like_capital_o(cell_img):
            return "O", 96
    if letter2 in {"?", "P", "F", "R", "D", "B"} or conf2 < 60:
        if looks_like_capital_p(cell_img):
            return "P", 96
        if letter2 == "P" and not looks_like_capital_p(cell_img):
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
    confidence_folder = "high_confidence" if confidence >= HIGH_CONFIDENCE_THRESHOLD and letter_folder != "UNKNOWN" else "low_confidence"
    output_dir = os.path.join(TEMPLATE_IMAGE_ROOT, confidence_folder, letter_folder)
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    conf_text = f"{int(confidence)}" if confidence is not None else "unknown"
    filename = f"{letter_folder}_conf_{conf_text}_row_{row}_col_{col}_{image_kind}_{timestamp}.png"
    return os.path.join(output_dir, filename)


def save_letter_template_images(cell_img, letter, confidence, row, col):
    if not SAVE_TEMPLATE_IMAGES:
        return
    if SAVE_CLEANED_TEMPLATE_IMAGE:
        cleaned = clean_cell_for_ocr(cell_img, threshold="otsu")
        cleaned.save(get_template_output_path(letter, confidence, row, col, "cleaned"))
    if SAVE_ORIGINAL_TEMPLATE_IMAGE:
        cell_img.save(get_template_output_path(letter, confidence, row, col, "original"))


def split_grid_into_cells(img, capture_region=None):
    img, crop_left, crop_top = auto_crop_white_card_with_offset(img)
    width, height = img.size
    pad_x = int(width * 0.04)
    pad_y = int(height * 0.04)
    img = img.crop((pad_x, pad_y, width - pad_x, height - pad_y))
    width, height = img.size
    cell_w = width / GRID_SIZE
    cell_h = height / GRID_SIZE
    cells = []
    cell_centers_screen = {}
    screen_left = capture_region["left"] if capture_region is not None else 0
    screen_top = capture_region["top"] if capture_region is not None else 0
    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            left = int(col * cell_w)
            top = int(row * cell_h)
            right = int((col + 1) * cell_w)
            bottom = int((row + 1) * cell_h)
            cell_img = img.crop((left, top, right, bottom))
            cells.append((row, col, cell_img))
            center_x = screen_left + crop_left + pad_x + left + (right - left) / 2
            center_y = screen_top + crop_top + pad_y + top + (bottom - top) / 2
            cell_centers_screen[(row, col)] = (int(center_x), int(center_y))
    return cells, cell_centers_screen


def extract_grid_from_image(img, capture_region=None):
    cells, cell_centers_screen = split_grid_into_cells(img, capture_region=capture_region)
    grid = [["?" for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    confidences = [[-1 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    max_workers = min(8, os.cpu_count() or 4)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(ocr_single_letter, cell_img): (row, col, cell_img) for row, col, cell_img in cells}
        for future in as_completed(future_map):
            row, col, cell_img = future_map[future]
            try:
                letter, conf = future.result()
            except Exception:
                letter, conf = "?", -1
            grid[row][col] = letter
            confidences[row][col] = conf
            save_letter_template_images(cell_img, letter, conf, row, col)
            if SAVE_FAILED_CELLS and (letter == "?" or conf < 30):
                cell_img.save(f"debug_failed_cell_{row}_{col}.png")
    if ASK_FOR_UNKNOWN_CELLS:
        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                if grid[row][col] == "?" or confidences[row][col] < 20:
                    val = input(f"Cell ({row}, {col}) was '{grid[row][col]}' with confidence {confidences[row][col]:.1f}. Enter letter: ").strip().upper()
                    if len(val) == 1 and val.isalpha():
                        grid[row][col] = val
                        confidences[row][col] = 100
    return grid, confidences, cell_centers_screen


def get_neighbors(row, col):
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nr = row + dr
            nc = col + dc
            if 0 <= nr < GRID_SIZE and 0 <= nc < GRID_SIZE:
                yield nr, nc


def find_connected_words(grid):
    results = []
    best_by_word = {}

    def dfs(row, col, word, positions, visited):
        if len(word) >= MIN_WORD_LENGTH and is_english_word(word):
            item = {
                "word": word,
                "start": positions[0],
                "end": positions[-1],
                "positions": positions.copy(),
                "length": len(word),
                "freq": zipf_frequency(word.lower(), "en"),
            }
            if ALLOW_DUPLICATE_WORDS_WITH_DIFFERENT_PATHS:
                results.append(item)
            else:
                existing = best_by_word.get(word)
                if existing is None or len(item["positions"]) < len(existing["positions"]):
                    best_by_word[word] = item
        if len(word) >= MAX_WORD_LENGTH:
            return
        for nr, nc in get_neighbors(row, col):
            if (nr, nc) in visited:
                continue
            next_letter = grid[nr][nc]
            if next_letter == "?":
                continue
            next_word = word + next_letter
            if not is_possible_word_prefix(next_word):
                continue
            visited.add((nr, nc))
            positions.append((nr, nc))
            dfs(nr, nc, next_word, positions, visited)
            positions.pop()
            visited.remove((nr, nc))

    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            letter = grid[r][c]
            if letter == "?" or not is_possible_word_prefix(letter):
                continue
            dfs(r, c, letter, [(r, c)], {(r, c)})
    if not ALLOW_DUPLICATE_WORDS_WITH_DIFFERENT_PATHS:
        results = list(best_by_word.values())
    results.sort(key=lambda item: (item["length"], item["freq"], item["word"]), reverse=True)
    return results


def order_words_for_fast_tracing(words, cell_centers_screen):
    if not ORDER_WORDS_TO_MINIMIZE_MOUSE_TRAVEL or len(words) <= 2:
        return words
    remaining = words.copy()
    ordered = []
    try:
        current_x, current_y = pyautogui.position()
    except Exception:
        first_start = remaining[0]["positions"][0]
        current_x, current_y = cell_centers_screen.get(first_start, (0, 0))
    while remaining:
        def distance_to_start(item):
            start_pos = item["positions"][0]
            sx, sy = cell_centers_screen.get(start_pos, (current_x, current_y))
            return (sx - current_x) ** 2 + (sy - current_y) ** 2
        best_index = min(range(len(remaining)), key=lambda i: distance_to_start(remaining[i]))
        chosen = remaining.pop(best_index)
        ordered.append(chosen)
        end_pos = chosen["positions"][-1]
        current_x, current_y = cell_centers_screen.get(end_pos, (current_x, current_y))
    return ordered


def interpolate_points(start_point, end_point, step_pixels=TRACE_STEP_PIXELS):
    x1, y1 = start_point
    x2, y2 = end_point
    dx = x2 - x1
    dy = y2 - y1
    distance = max((dx * dx + dy * dy) ** 0.5, 1)
    steps = max(1, int(distance / max(step_pixels, 1)))
    for i in range(1, steps + 1):
        t = i / steps
        yield int(round(x1 + dx * t)), int(round(y1 + dy * t))


def trace_word_on_screen(word_item, cell_centers_screen):
    positions = word_item["positions"]
    points = []
    for pos in positions:
        if pos not in cell_centers_screen:
            print("Could not trace word because some cell positions are missing.")
            return
        x, y = cell_centers_screen[pos]
        points.append((x + TRACE_CLICK_OFFSET_X, y + TRACE_CLICK_OFFSET_Y))
    word = word_item["word"]
    print(f"\nTracing word: {word}")
    print(f"Path: {points}")
    start_x, start_y = points[0]
    try:
        pyautogui.moveTo(start_x, start_y, duration=TRACE_MOVE_DURATION)
        time.sleep(TRACE_START_HOLD_SECONDS)
        pyautogui.mouseDown(button=TRACE_MOUSE_BUTTON)
        time.sleep(TRACE_START_HOLD_SECONDS)
        current_point = points[0]
        for next_point in points[1:]:
            for x, y in interpolate_points(current_point, next_point):
                pyautogui.moveTo(x, y, duration=TRACE_STEP_DURATION)
            pyautogui.moveTo(next_point[0], next_point[1], duration=TRACE_STEP_DURATION)
            time.sleep(TRACE_TILE_HOLD_SECONDS)
            current_point = next_point
        time.sleep(TRACE_END_HOLD_SECONDS)
    finally:
        pyautogui.mouseUp(button=TRACE_MOUSE_BUTTON)
    time.sleep(TRACE_PAUSE_AFTER_WORD)


def show_manual_grid_picker(initial_grid=None):
    root = tk.Tk()
    root.title("Manual 4x4 Letter Picker")
    root.attributes("-topmost", True)
    result = {"grid": None, "cancelled": False}
    frame = tk.Frame(root, padx=14, pady=14)
    frame.pack(fill="both", expand=True)
    title = tk.Label(frame, text="Enter the letters in the same positions as the game grid", font=("Arial", 13, "bold"))
    title.grid(row=0, column=0, columnspan=GRID_SIZE, pady=(0, 8))
    help_text = tk.Label(frame, text="One letter per box. Press Enter or click Start when all 16 are filled.", font=("Arial", 10))
    help_text.grid(row=1, column=0, columnspan=GRID_SIZE, pady=(0, 12))
    entries = []

    def clean_entry_value(value):
        return re.sub(r"[^A-Z]", "", value.upper())[:1]

    def focus_next(index):
        if index + 1 < GRID_SIZE * GRID_SIZE:
            entries[index + 1].focus_set()
            entries[index + 1].selection_range(0, tk.END)

    def focus_prev(index):
        if index - 1 >= 0:
            entries[index - 1].focus_set()
            entries[index - 1].selection_range(0, tk.END)

    def on_key_release(event, index):
        if event.keysym in {"BackSpace", "Delete", "Left", "Right", "Tab", "ISO_Left_Tab", "Return", "Escape"}:
            return
        entry = entries[index]
        cleaned = clean_entry_value(entry.get())
        entry.delete(0, tk.END)
        entry.insert(0, cleaned)
        if cleaned:
            focus_next(index)

    def on_backspace(event, index):
        entry = entries[index]
        if entry.get():
            entry.delete(0, tk.END)
        else:
            focus_prev(index)
        return "break"

    def collect_grid():
        grid = []
        missing = []
        for r in range(GRID_SIZE):
            row = []
            for c in range(GRID_SIZE):
                idx = r * GRID_SIZE + c
                val = clean_entry_value(entries[idx].get())
                if not val:
                    missing.append((r, c))
                    row.append("?")
                else:
                    row.append(val)
            grid.append(row)
        return grid, missing

    status = tk.Label(frame, text="", fg="red", font=("Arial", 10, "bold"))
    status.grid(row=GRID_SIZE + 3, column=0, columnspan=GRID_SIZE, pady=(8, 0))

    def confirm():
        grid, missing = collect_grid()
        if missing:
            status.config(text=f"Please fill all 16 cells. Missing: {missing[0]}")
            r, c = missing[0]
            entries[r * GRID_SIZE + c].focus_set()
            return
        result["grid"] = grid
        root.destroy()

    def cancel():
        result["cancelled"] = True
        root.destroy()

    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            idx = r * GRID_SIZE + c
            entry = tk.Entry(frame, width=3, justify="center", font=("Arial", 24, "bold"))
            entry.grid(row=r + 2, column=c, padx=5, pady=5)
            if initial_grid and r < len(initial_grid) and c < len(initial_grid[r]):
                val = clean_entry_value(str(initial_grid[r][c]))
                if val != "?":
                    entry.insert(0, val)
            entry.bind("<KeyRelease>", lambda event, i=idx: on_key_release(event, i))
            entry.bind("<BackSpace>", lambda event, i=idx: on_backspace(event, i))
            entries.append(entry)
    button_frame = tk.Frame(frame)
    button_frame.grid(row=GRID_SIZE + 2, column=0, columnspan=GRID_SIZE, pady=(12, 0))
    tk.Button(button_frame, text="Start", command=confirm, font=("Arial", 12, "bold"), width=10).pack(side="left", padx=5)
    tk.Button(button_frame, text="Cancel", command=cancel, font=("Arial", 12), width=10).pack(side="left", padx=5)
    root.bind("<Return>", lambda event: confirm())
    root.bind("<Escape>", lambda event: cancel())
    entries[0].focus_set()
    root.mainloop()
    if result["cancelled"] or result["grid"] is None:
        raise RuntimeError("Manual grid entry cancelled.")
    confidences = [[100 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    return result["grid"], confidences


def get_cell_centers_from_capture_region(capture_region):
    img = capture_screen_region(capture_region)
    _, cell_centers_screen = split_grid_into_cells(img, capture_region=capture_region)
    return cell_centers_screen


def print_grid(grid, confidences=None):
    print("\nDetected grid:")
    for row in grid:
        print(" ".join(row))
    if confidences:
        print("\nConfidence:")
        for row in confidences:
            print(" ".join(f"{int(c):2d}" for c in row))


def show_continue_or_quit_popup():
    root = tk.Tk()
    root.title("Continue?")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    choice = {"continue": False}
    frame = tk.Frame(root, padx=22, pady=18)
    frame.pack(fill="both", expand=True)
    label = tk.Label(frame, text=f"Paused after {PAUSE_POPUP_EVERY_SECONDS} seconds.\nDo you want to continue?", font=("Arial", 12), justify="center")
    label.pack(pady=(0, 14))
    button_frame = tk.Frame(frame)
    button_frame.pack()

    def continue_clicked():
        choice["continue"] = True
        root.destroy()

    def quit_clicked():
        choice["continue"] = False
        root.destroy()

    tk.Button(button_frame, text="Continue", command=continue_clicked, width=12, font=("Arial", 11, "bold")).pack(side="left", padx=6)
    tk.Button(button_frame, text="Quit", command=quit_clicked, width=12, font=("Arial", 11)).pack(side="left", padx=6)
    root.bind("<Return>", lambda event: continue_clicked())
    root.bind("<space>", lambda event: continue_clicked())
    root.bind("<Escape>", lambda event: quit_clicked())
    root.update_idletasks()
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    win_w = root.winfo_width()
    win_h = root.winfo_height()
    root.geometry(f"+{int((screen_w - win_w) / 2)}+{int((screen_h - win_h) / 2)}")
    root.focus_force()
    root.mainloop()
    return choice["continue"]


def maybe_pause_for_continue(last_pause_popup_time):
    if not PAUSE_POPUP_ENABLED:
        return True, last_pause_popup_time
    if time.time() - last_pause_popup_time < PAUSE_POPUP_EVERY_SECONDS:
        return True, last_pause_popup_time
    should_continue = show_continue_or_quit_popup()
    if not should_continue:
        print("\nStopped by user from pause popup.")
        return False, last_pause_popup_time
    return True, time.time()


def print_and_trace_words(grid, confidences, cell_centers_screen, last_pause_popup_time=None):
    words = find_connected_words(grid)
    print_grid(grid, confidences)
    print(f"\nConnected English words found, length >= {MIN_WORD_LENGTH}:")
    if words:
        for item in words:
            print(f"{item['word']} | len={item['length']} | start={item['start']} | end={item['end']} | path={item['positions']}")
    else:
        print("None found.")
    if last_pause_popup_time is None:
        last_pause_popup_time = time.time()
    should_continue = True
    if AUTO_TRACE_FOUND_WORD and words:
        words_to_trace = order_words_for_fast_tracing(words[:MAX_WORDS_TO_TRACE], cell_centers_screen)
        if TRACE_ONLY_FIRST_WORD:
            should_continue, last_pause_popup_time = maybe_pause_for_continue(last_pause_popup_time)
            if should_continue:
                trace_word_on_screen(words_to_trace[0], cell_centers_screen)
        else:
            print(f"\nTracing up to {len(words_to_trace)} words...")
            for item in words_to_trace:
                should_continue, last_pause_popup_time = maybe_pause_for_continue(last_pause_popup_time)
                if not should_continue:
                    break
                trace_word_on_screen(item, cell_centers_screen)
    return words, should_continue, last_pause_popup_time


def run_auto_capture():
    global CAPTURE_REGION
    print("Align the guide around the full 4x4 letter grid, then click Start.")
    CAPTURE_REGION = show_capture_guide(CAPTURE_REGION)
    print("\nUsing capture region:")
    print(CAPTURE_REGION)
    print("Move mouse to the top-left corner to stop pyautogui actions.")
    if USE_MANUAL_GRID_PICKER:
        print("\nManual grid picker mode is ON.")
        print("You will type the exact position of all 16 letters.")
        initial_grid = None
        if MANUAL_GRID_PREFILL_WITH_OCR:
            print("\nTrying OCR once to prefill the manual grid...")
            img = capture_screen_region(CAPTURE_REGION)
            initial_grid, _, cell_centers_screen = extract_grid_from_image(img, capture_region=CAPTURE_REGION)
        else:
            cell_centers_screen = get_cell_centers_from_capture_region(CAPTURE_REGION)
        last_pause_popup_time = time.time()
        try:
            while True:
                if PAUSE_POPUP_ENABLED and time.time() - last_pause_popup_time >= PAUSE_POPUP_EVERY_SECONDS:
                    should_continue = show_continue_or_quit_popup()
                    if not should_continue:
                        print("\nStopped by user from pause popup.")
                        break
                    last_pause_popup_time = time.time()
                start_time = time.time()
                grid, confidences = show_manual_grid_picker(initial_grid=initial_grid)
                _, should_continue, last_pause_popup_time = print_and_trace_words(grid, confidences, cell_centers_screen, last_pause_popup_time=last_pause_popup_time)
                if not should_continue:
                    break
                elapsed = time.time() - start_time
                print(f"\nRun time: {elapsed:.2f}s")
                print("-" * 40)
                if MANUAL_GRID_RUN_ONCE:
                    print("Manual grid run complete.")
                    break
                initial_grid = grid
        except KeyboardInterrupt:
            print("\nStopped.")
        return
    print(f"\nAuto-capturing every {AUTO_CAPTURE_SECONDS} seconds.")
    print("Press Ctrl+C to stop.")
    if SAVE_TEMPLATE_IMAGES:
        print(f"Saving template images under: {TEMPLATE_IMAGE_ROOT}")
    print()
    last_pause_popup_time = time.time()
    try:
        while True:
            if PAUSE_POPUP_ENABLED and time.time() - last_pause_popup_time >= PAUSE_POPUP_EVERY_SECONDS:
                should_continue = show_continue_or_quit_popup()
                if not should_continue:
                    print("\nStopped by user from pause popup.")
                    break
                last_pause_popup_time = time.time()
            start_time = time.time()
            img = capture_screen_region(CAPTURE_REGION)
            grid, confidences, cell_centers_screen = extract_grid_from_image(img, capture_region=CAPTURE_REGION)
            _, should_continue, last_pause_popup_time = print_and_trace_words(grid, confidences, cell_centers_screen, last_pause_popup_time=last_pause_popup_time)
            if not should_continue:
                break
            elapsed = time.time() - start_time
            print(f"\nScan time: {elapsed:.2f}s")
            print("-" * 40)
            sleep_time = max(0, AUTO_CAPTURE_SECONDS - elapsed)
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    """Run the interactive Word Blitz auto-player."""
    run_auto_capture()


if __name__ == "__main__":
    main()
