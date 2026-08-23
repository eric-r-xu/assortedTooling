"""Find playable five-letter StackDown words from a selected screen region.

The lowest horizontal row of tiles is treated as the five-letter guess.  Tiles
above it are available only when computer vision can see a convex quadrilateral
for their complete outer edge (in other words, all four corners are visible).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
import re
import sys
import time
import tkinter as tk

import cv2
import mss
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract
from wordfreq import top_n_list, zipf_frequency


CAPTURE_REGION = {"left": 200, "top": 100, "width": 500, "height": 700}
CAPTURE_INTERVAL_SECONDS = 1.0
MIN_GUIDE_WIDTH = 240
MIN_GUIDE_HEIGHT = 300
MIN_TILE_AREA_RATIO = 0.0015
MAX_TILE_AREA_RATIO = 0.10
MIN_TILE_SIDE_RATIO = 0.075
MAX_TILE_SIDE_RATIO = 0.30
MIN_WORD_FREQUENCY = 2.5
WORD_LIST_SIZE = 200_000
OCR_CONFIG = (
    "--psm 10 --oem 3 "
    "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0 "
    "-c load_system_dawg=0 -c load_freq_dawg=0"
)


@dataclass(frozen=True)
class Tile:
    corners: np.ndarray
    center: tuple[float, float]
    width: float
    height: float
    area: float
    letter: str = "?"
    confidence: float = -1.0


def show_capture_guide(region: dict[str, int]) -> dict[str, int]:
    """Show a movable, freely resizable rectangle and return screen coordinates."""
    root = tk.Tk()
    root.title("StackDown capture region")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.78)
    root.geometry(
        f"{region['width']}x{region['height']}+{region['left']}+{region['top']}"
    )
    state = {"cancelled": False, "drag_x": 0, "drag_y": 0, "mode": "move"}

    canvas = tk.Canvas(root, bg="#e9f5ff", highlightthickness=0, cursor="fleur")
    canvas.pack(fill="both", expand=True)

    def redraw(event=None):
        width = root.winfo_width()
        height = root.winfo_height()
        canvas.delete("all")
        canvas.create_rectangle(3, 3, width - 4, height - 4, outline="#e53935", width=6)
        canvas.create_text(
            width / 2,
            30,
            text="Cover the entire stack and the five guess slots",
            fill="#111111",
            font=("Arial", 14, "bold"),
        )
        canvas.create_text(
            width / 2,
            56,
            text="Drag to move  •  drag lower-right corner to resize",
            fill="#111111",
            font=("Arial", 11),
        )
        canvas.create_text(
            width / 2,
            78,
            text="Enter: start  •  Esc: cancel",
            fill="#111111",
            font=("Arial", 11),
        )
        canvas.create_polygon(
            width - 32,
            height - 4,
            width - 4,
            height - 32,
            width - 4,
            height - 4,
            fill="#e53935",
        )

    def press(event):
        state["drag_x"] = event.x_root
        state["drag_y"] = event.y_root
        near_handle = event.x >= root.winfo_width() - 45 and event.y >= root.winfo_height() - 45
        state["mode"] = "resize" if near_handle else "move"
        canvas.configure(cursor="bottom_right_corner" if near_handle else "fleur")

    def drag(event):
        dx = event.x_root - state["drag_x"]
        dy = event.y_root - state["drag_y"]
        state["drag_x"] = event.x_root
        state["drag_y"] = event.y_root
        if state["mode"] == "resize":
            width = max(MIN_GUIDE_WIDTH, root.winfo_width() + dx)
            height = max(MIN_GUIDE_HEIGHT, root.winfo_height() + dy)
            root.geometry(f"{width}x{height}")
        else:
            root.geometry(f"+{root.winfo_x() + dx}+{root.winfo_y() + dy}")

    def confirm(event=None):
        region.update(
            left=root.winfo_x(),
            top=root.winfo_y(),
            width=root.winfo_width(),
            height=root.winfo_height(),
        )
        root.withdraw()
        root.update()
        time.sleep(0.35)
        root.destroy()

    def cancel(event=None):
        state["cancelled"] = True
        root.destroy()

    canvas.bind("<Configure>", redraw)
    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    root.bind("<Return>", confirm)
    root.bind("<space>", confirm)
    root.bind("<Escape>", cancel)
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.after(100, root.focus_force)
    root.mainloop()
    if state["cancelled"]:
        raise RuntimeError("Capture selection cancelled.")
    return region


def capture_region(region: dict[str, int]) -> Image.Image:
    with mss.mss() as screen:
        shot = screen.grab(region)
    return Image.frombytes("RGB", shot.size, shot.rgb)


def _order_corners(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).ravel()
    ordered[0] = points[np.argmin(sums)]       # top-left
    ordered[2] = points[np.argmax(sums)]       # bottom-right
    ordered[1] = points[np.argmin(differences)]  # top-right
    ordered[3] = points[np.argmax(differences)]  # bottom-left
    return ordered


def _corner_angles(corners: np.ndarray) -> list[float]:
    angles = []
    for index in range(4):
        previous = corners[(index - 1) % 4] - corners[index]
        following = corners[(index + 1) % 4] - corners[index]
        denominator = np.linalg.norm(previous) * np.linalg.norm(following)
        if denominator == 0:
            return []
        cosine = float(np.clip(np.dot(previous, following) / denominator, -1, 1))
        angles.append(math.degrees(math.acos(cosine)))
    return angles


def _quad_iou(first: np.ndarray, second: np.ndarray) -> float:
    first_box = cv2.boundingRect(first.astype(np.int32))
    second_box = cv2.boundingRect(second.astype(np.int32))
    ax1, ay1, aw, ah = first_box
    bx1, by1, bw, bh = second_box
    ax2, ay2, bx2, by2 = ax1 + aw, ay1 + ah, bx1 + bw, by1 + bh
    overlap = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0, min(ay2, by2) - max(ay1, by1)
    )
    union = aw * ah + bw * bh - overlap
    return overlap / union if union else 0.0


def _same_physical_tile(first: Tile, second: Tile) -> bool:
    if _quad_iou(first.corners, second.corners) > 0.55:
        return True
    center_distance = math.dist(first.center, second.center)
    smaller_side = min(first.width, first.height, second.width, second.height)
    return center_distance < smaller_side * 0.28


def _tile_edge_maps(rgb: np.ndarray) -> list[np.ndarray]:
    """Build complementary edge maps for bright, dark, and colored tile borders."""
    channels = [
        cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY),
        *cv2.split(rgb),
        *cv2.split(cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV))[1:],
    ]
    maps = []
    for channel in channels:
        blurred = cv2.GaussianBlur(channel, (5, 5), 0)
        for low, high in ((25, 85), (45, 140)):
            edges = cv2.Canny(blurred, low, high)
            edges = cv2.morphologyEx(
                edges,
                cv2.MORPH_CLOSE,
                np.ones((3, 3), np.uint8),
                iterations=2,
            )
            maps.append(edges)

    # A slightly wider close reconnects borders interrupted by highlights or
    # shadows. It does not invent corners: candidates still need four sane
    # corner angles and a convex tile-shaped outline below.
    combined = np.maximum.reduce(maps)
    maps.append(
        cv2.morphologyEx(
            combined,
            cv2.MORPH_CLOSE,
            np.ones((5, 5), np.uint8),
            iterations=1,
        )
    )

    # StackDown's exposed tile faces are substantially lighter and less
    # saturated than the green playfield. Their filled regions are a useful
    # independent signal when shadows or neighboring tiles break an edge.
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturation, value = hsv[:, :, 1], hsv[:, :, 2]
    light_face = np.where((value >= 135) & (saturation <= 125), 255, 0).astype(np.uint8)
    light_face = cv2.morphologyEx(
        light_face, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1
    )
    maps.append(light_face)
    maps.append(cv2.Canny(light_face, 20, 70))

    # Currently playable StackDown tiles use a much lighter cream face than
    # buried tiles. Keep this stricter mask separate so adjacent beige layers
    # cannot merge it into one large stack-shaped component.
    bright_face = np.where((value >= 225) & (saturation <= 90), 255, 0).astype(np.uint8)
    bright_face = cv2.morphologyEx(
        bright_face, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1
    )
    maps.append(bright_face)
    maps.append(cv2.Canny(bright_face, 15, 55))
    return maps


def _four_corner_polygon(contour: np.ndarray) -> np.ndarray | None:
    """Approximate rounded or shadowed tile borders by their four corners."""
    perimeter = cv2.arcLength(contour, True)
    for epsilon_ratio in (0.018, 0.025, 0.035, 0.045, 0.060):
        polygon = cv2.approxPolyDP(contour, epsilon_ratio * perimeter, True)
        if len(polygon) == 4 and cv2.isContourConvex(polygon):
            return polygon
    return None


def _bright_face_fraction(rgb: np.ndarray, tile: Tile) -> float:
    """Measure the light cream playable-face color inside a tile's corners."""
    center = np.mean(tile.corners, axis=0)
    inner = center + (tile.corners - center) * 0.78
    mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, inner.astype(np.int32), 255)
    pixels = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[mask > 0]
    if pixels.size == 0:
        return 0.0
    return float(np.mean((pixels[:, 2] >= 220) & (pixels[:, 1] <= 100)))


def detect_fully_visible_tiles(image: Image.Image) -> list[Tile]:
    """Return tile-like convex quadrilaterals whose four outer corners are visible."""
    rgb = np.asarray(image.convert("RGB"))
    contours = []
    for edges in _tile_edge_maps(rgb):
        found, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours.extend(found)
    image_area = image.width * image.height
    # The selector is designed around a tall phone/game rectangle. Width is a
    # more stable proxy for tile scale than min(width, height), especially in
    # cropped landscape test images.
    reference_side = image.width
    minimum_tile_side = reference_side * MIN_TILE_SIDE_RATIO
    maximum_tile_side = reference_side * MAX_TILE_SIDE_RATIO
    candidates: list[Tile] = []

    for contour in contours:
        area = abs(cv2.contourArea(contour))
        if not MIN_TILE_AREA_RATIO * image_area <= area <= MAX_TILE_AREA_RATIO * image_area:
            continue
        polygon = _four_corner_polygon(contour)
        if polygon is None:
            continue
        corners = _order_corners(polygon.reshape(4, 2))
        angles = _corner_angles(corners)
        if not angles or any(angle < 55 or angle > 125 for angle in angles):
            continue
        top = np.linalg.norm(corners[1] - corners[0])
        bottom = np.linalg.norm(corners[2] - corners[3])
        left = np.linalg.norm(corners[3] - corners[0])
        right = np.linalg.norm(corners[2] - corners[1])
        width, height = (top + bottom) / 2, (left + right) / 2
        if height == 0 or not 0.55 <= width / height <= 1.55:
            continue
        if not (
            minimum_tile_side <= width <= maximum_tile_side
            and minimum_tile_side <= height <= maximum_tile_side
        ):
            continue
        center = tuple(np.mean(corners, axis=0).tolist())
        candidates.append(Tile(corners, center, width, height, area))

    # Edge detection often finds both sides of one thick border. Keep the
    # larger outer quadrilateral, which is the one relevant to corner exposure.
    candidates.sort(key=lambda tile: tile.area, reverse=True)
    unique: list[Tile] = []
    for candidate in candidates:
        if any(_same_physical_tile(candidate, kept) for kept in unique):
            continue
        unique.append(candidate)

    # Tile-sized shapes repeat throughout the board. Large polygons spanning
    # several tiles do not. Prefer the size family with the most supporting
    # detections, but only when there is enough evidence to establish one.
    if len(unique) >= 4:
        support = []
        for candidate in unique:
            matches = sum(
                abs(other.width - candidate.width) <= candidate.width * 0.28
                and abs(other.height - candidate.height) <= candidate.height * 0.28
                for other in unique
            )
            support.append(matches)
        best_support = max(support)
        if best_support >= 4:
            anchors = [
                tile for tile, count in zip(unique, support) if count == best_support
            ]
            anchor = min(anchors, key=lambda tile: tile.area)
            matching_size = [
                tile
                for tile in unique
                if abs(tile.width - anchor.width) <= anchor.width * 0.32
                and abs(tile.height - anchor.height) <= anchor.height * 0.32
            ]
            if len(matching_size) >= 4:
                unique = matching_size

    highlighted = [tile for tile in unique if _bright_face_fraction(rgb, tile) >= 0.38]
    if highlighted:
        unique = highlighted
    return sorted(unique, key=lambda tile: (tile.center[1], tile.center[0]))


def crop_tile(image: Image.Image, tile: Tile, inset_ratio: float = 0.12) -> Image.Image:
    size = max(80, int(round(max(tile.width, tile.height))))
    destination = np.array(
        [[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(tile.corners.astype(np.float32), destination)
    warped = cv2.warpPerspective(np.asarray(image.convert("RGB")), transform, (size, size))
    inset = max(2, int(size * inset_ratio))
    warped = warped[inset : size - inset, inset : size - inset]
    return Image.fromarray(warped)


def clean_for_ocr(tile_image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(tile_image)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(2.5).filter(ImageFilter.SHARPEN)
    array = np.asarray(gray)
    threshold, binary = cv2.threshold(array, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Make the background white regardless of tile theme.
    if np.mean(binary == 0) > 0.50:
        binary = 255 - binary
    border = max(1, int(min(binary.shape) * 0.08))
    interior = binary[border:-border, border:-border]
    if interior.size == 0:
        interior = binary
    return Image.fromarray(interior).resize((240, 240), Image.Resampling.LANCZOS)


def looks_like_capital_p(cleaned: Image.Image) -> bool:
    """Recognize the StackDown P shape when Tesseract returns no character."""
    dark = np.asarray(cleaned) < 90
    ys, xs = np.where(dark)
    if len(xs) == 0 or len(ys) == 0:
        return False
    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()
    width, height = x_max - x_min + 1, y_max - y_min + 1
    if height <= 0 or not 0.28 <= width / height <= 1.05:
        return False
    letter = dark[y_min : y_max + 1, x_min : x_max + 1]
    normalized = cv2.resize(
        letter.astype(np.uint8), (64, 64), interpolation=cv2.INTER_NEAREST
    ).astype(bool)
    return (
        normalized[:, :16].mean() > 0.20
        and normalized[:18, :52].mean() > 0.10
        and normalized[20:42, :52].mean() > 0.08
        and normalized[8:36, 34:].mean() > 0.06
        and normalized[42:, 28:].mean() < 0.10
        and normalized[42:, :22].mean() > 0.06
    )


def _central_letter_mask(cleaned: Image.Image) -> np.ndarray | None:
    """Extract the central connected glyph while ignoring tile-border remnants."""
    dark = (np.asarray(cleaned) < 90).astype(np.uint8)
    height, width = dark.shape
    margin_x, margin_y = int(width * 0.10), int(height * 0.10)
    dark[:margin_y, :] = 0
    dark[height - margin_y :, :] = 0
    dark[:, :margin_x] = 0
    dark[:, width - margin_x :] = 0
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(dark, 8)
    choices = []
    for label in range(1, count):
        x, y, component_width, component_height, area = stats[label]
        center_x, center_y = centroids[label]
        if area < 80 or component_height < height * 0.18:
            continue
        center_distance = math.hypot(center_x - width / 2, center_y - height / 2)
        choices.append((area - center_distance * 2, label, x, y, component_width, component_height))
    if not choices:
        return None
    _, label, x, y, component_width, component_height = max(choices)
    component = labels[y : y + component_height, x : x + component_width] == label
    return cv2.resize(
        component.astype(np.uint8), (64, 64), interpolation=cv2.INTER_NEAREST
    ).astype(bool)


def looks_like_capital_z(cleaned: Image.Image) -> bool:
    """Distinguish StackDown's block Z from Tesseract's occasional A guess."""
    letter = _central_letter_mask(cleaned)
    if letter is None:
        return False
    top_density = letter[:13, :].mean()
    bottom_density = letter[51:, :].mean()
    top_width_coverage = letter[:13, :].any(axis=0).mean()
    bottom_width_coverage = letter[51:, :].any(axis=0).mean()
    upper_diagonal = letter[14:31, 30:61]
    lower_diagonal = letter[33:50, 3:34]

    row_centers = []
    for row in range(12, 52):
        xs = np.where(letter[row])[0]
        if len(xs):
            row_centers.append((row, float(xs.mean())))
    if len(row_centers) < 20:
        return False
    upper_x = np.mean([x for row, x in row_centers if 16 <= row <= 29])
    lower_x = np.mean([x for row, x in row_centers if 35 <= row <= 48])
    return (
        top_density > 0.16
        and bottom_density > 0.16
        and top_width_coverage > 0.68
        and bottom_width_coverage > 0.68
        and upper_diagonal.mean() > 0.08
        and lower_diagonal.mean() > 0.08
        and upper_x - lower_x > 8.0
    )


def looks_like_capital_i(cleaned: Image.Image) -> bool:
    """Recognize StackDown's narrow, centered capital I glyph."""
    dark = (np.asarray(cleaned) < 90).astype(np.uint8)
    height, width = dark.shape
    margin_x, margin_y = int(width * 0.10), int(height * 0.10)
    dark[:margin_y, :] = 0
    dark[height - margin_y :, :] = 0
    dark[:, :margin_x] = 0
    dark[:, width - margin_x :] = 0
    count, _, stats, centroids = cv2.connectedComponentsWithStats(dark, 8)
    choices = []
    for label in range(1, count):
        x, y, component_width, component_height, area = stats[label]
        center_x, center_y = centroids[label]
        if area < 60 or component_height < height * 0.18:
            continue
        center_distance = math.hypot(center_x - width / 2, center_y - height / 2)
        choices.append((area - center_distance * 2, component_width, component_height, center_x))
    if not choices:
        return False
    _, component_width, component_height, center_x = max(choices)
    return (
        component_width / component_height < 0.38
        and component_height > height * 0.24
        and width * 0.36 < center_x < width * 0.64
    )


def looks_like_capital_o(cleaned: Image.Image) -> bool:
    """Recognize the closed round O used on light StackDown tiles."""
    letter = _central_letter_mask(cleaned)
    if letter is None:
        return False
    left = letter[:, :15].mean()
    right = letter[:, 49:].mean()
    top = letter[:15, :].mean()
    bottom = letter[49:, :].mean()
    center = letter[21:43, 21:43].mean()
    return (
        left > 0.10
        and right > 0.10
        and top > 0.10
        and bottom > 0.10
        and center < 0.13
        and abs(left - right) < 0.22
        and abs(top - bottom) < 0.22
    )


def looks_like_capital_n(cleaned: Image.Image) -> bool:
    """Recognize an N from its two stems and descending interior diagonal."""
    letter = _central_letter_mask(cleaned)
    if letter is None:
        return False
    left_stem = letter[:, :14].mean()
    right_stem = letter[:, 50:].mean()

    diagonal_points = []
    for row in range(7, 57):
        xs = np.where(letter[row, 14:50])[0]
        if len(xs):
            diagonal_points.append((row, float(xs.mean() + 14)))
    if len(diagonal_points) < 22:
        return False
    rows = np.array([point[0] for point in diagonal_points], dtype=np.float32)
    columns = np.array([point[1] for point in diagonal_points], dtype=np.float32)
    correlation = float(np.corrcoef(rows, columns)[0, 1])
    upper_x = float(np.mean(columns[rows < 28]))
    lower_x = float(np.mean(columns[rows > 36]))
    center_diagonal = letter[21:43, 21:43].mean()
    return (
        left_stem > 0.16
        and right_stem > 0.16
        and center_diagonal > 0.08
        and correlation > 0.55
        and lower_x - upper_x > 6.0
    )


def looks_like_capital_b(cleaned: Image.Image) -> bool:
    """Recognize B by its left stem and two enclosed bowl counters."""
    letter = _central_letter_mask(cleaned)
    if letter is None:
        return False
    background = (~letter).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(background, 8)
    enclosed_holes = 0
    for label in range(1, count):
        x, y, width, height, area = stats[label]
        touches_edge = x == 0 or y == 0 or x + width == 64 or y + height == 64
        if not touches_edge and area >= 24:
            enclosed_holes += 1
    left_stem = letter[:, :14].mean()
    upper_right = letter[5:31, 42:].mean()
    lower_right = letter[33:59, 42:].mean()
    return (
        enclosed_holes == 2
        and left_stem > 0.22
        and upper_right > 0.08
        and lower_right > 0.08
    )


def looks_like_capital_f(cleaned: Image.Image) -> bool:
    """Distinguish StackDown's F from Tesseract's low-confidence E guess."""
    letter = _central_letter_mask(cleaned)
    if letter is None:
        return False
    left_stem = letter[:, :14].mean()
    top_coverage = letter[:14, :].any(axis=0).mean()
    middle_coverage = letter[22:42, :].any(axis=0).mean()
    lower_right = letter[48:, 38:].mean()
    return (
        left_stem > 0.45
        and top_coverage > 0.72
        and middle_coverage > 0.58
        and lower_right < 0.08
    )


def recognize_letter(tile_image: Image.Image) -> tuple[str, float]:
    cleaned = clean_for_ocr(tile_image)
    ink_ratio = float(np.mean(np.asarray(cleaned) < 128))
    if ink_ratio < 0.015:
        return "?", -1.0
    data = pytesseract.image_to_data(
        cleaned, config=OCR_CONFIG, output_type=pytesseract.Output.DICT
    )
    best_letter, best_confidence = "?", -1.0
    for text, confidence in zip(data.get("text", []), data.get("conf", [])):
        text = re.sub(r"[^A-Z]", "", text.upper().replace("0", "O"))
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = -1.0
        if len(text) == 1 and confidence > best_confidence:
            best_letter, best_confidence = text, confidence
    if best_letter in {"?", "L", "T"} and looks_like_capital_i(cleaned):
        return "I", 96.0
    if best_letter in {"?", "C", "D", "Q"} and looks_like_capital_o(cleaned):
        return "O", 96.0
    if best_letter in {"?", "H", "M", "W"} and looks_like_capital_n(cleaned):
        return "N", 95.0
    if best_letter in {"?", "A"} and looks_like_capital_z(cleaned):
        return "Z", 94.0
    if best_letter in {"?", "P", "R"} and looks_like_capital_b(cleaned):
        return "B", 95.0
    # StackDown's F also satisfies the deliberately broad missing-P shape
    # heuristic. Trust an explicit F from Tesseract instead of overriding it.
    if best_letter in {"?", "A"} and looks_like_capital_p(cleaned):
        return "P", 94.0
    if best_letter == "E" and looks_like_capital_f(cleaned):
        return "F", 94.0
    return best_letter, best_confidence


def recognize_tiles(image: Image.Image, tiles: list[Tile]) -> list[Tile]:
    recognized = []
    for tile in tiles:
        letter, confidence = recognize_letter(crop_tile(image, tile))
        recognized.append(
            Tile(
                tile.corners,
                tile.center,
                tile.width,
                tile.height,
                tile.area,
                letter,
                confidence,
            )
        )
    return recognized


def identify_guess_row(
    tiles: list[Tile], capture_height: int | None = None
) -> tuple[list[Tile], list[Tile]]:
    """Split out entered letters in the lower five-slot guess row.

    Empty guess slots are not light tiles, so the detected bottom row can
    contain anywhere from one to five letters. A large vertical gap separates
    those entered letters from exposed tiles in the stack above.
    """
    if not tiles:
        return [], []
    median_height = float(np.median([tile.height for tile in tiles]))
    tolerance = max(8.0, median_height * 0.42)
    rows: list[list[Tile]] = []
    for tile in sorted(tiles, key=lambda item: item.center[1], reverse=True):
        matching = next(
            (row for row in rows if abs(np.mean([t.center[1] for t in row]) - tile.center[1]) <= tolerance),
            None,
        )
        if matching is None:
            rows.append([tile])
        else:
            matching.append(tile)
    rows.sort(key=lambda row: np.mean([tile.center[1] for tile in row]))
    guess = []
    if len(rows) >= 2:
        bottom = rows[-1]
        bottom_y = float(np.mean([tile.center[1] for tile in bottom]))
        previous_y = float(np.mean([tile.center[1] for tile in rows[-2]]))
        large_gap = bottom_y - previous_y >= median_height * 1.45
        in_guess_slot_zone = bool(
            capture_height
            and bottom_y >= capture_height * 0.78
            and previous_y < capture_height * 0.78
            and bottom_y - previous_y >= median_height * 0.80
        )
        separated_from_stack = large_gap or in_guess_slot_zone
        if 1 <= len(bottom) <= 5 and separated_from_stack:
            guess = bottom
    guess_ids = {id(tile) for tile in guess}
    guess = sorted(guess, key=lambda tile: tile.center[0])[:5]
    # Availability is a set for word matching, but column-major order makes
    # diagnostics deterministic and mirrors how exposed stack edges are read.
    visible = [tile for tile in tiles if id(tile) not in guess_ids]
    columns: list[list[Tile]] = []
    column_tolerance = median_height * 0.35
    for tile in sorted(visible, key=lambda item: item.center[0]):
        matching = next(
            (
                column
                for column in columns
                if abs(np.mean([item.center[0] for item in column]) - tile.center[0])
                <= column_tolerance
            ),
            None,
        )
        if matching is None:
            columns.append([tile])
        else:
            matching.append(tile)
    columns.sort(key=lambda column: np.mean([tile.center[0] for tile in column]))
    visible = [
        tile
        for column in columns
        for tile in sorted(column, key=lambda item: item.center[1])
    ]
    return guess, visible


def guess_prefix(guess_tiles: list[Tile]) -> str:
    prefix = []
    for tile in guess_tiles:
        if tile.letter == "?":
            break
        prefix.append(tile.letter)
    return "".join(prefix[:5])


def normalize_word(raw_word: str) -> str:
    word = raw_word.strip().upper()
    return word if re.fullmatch(r"[A-Z]{5}", word) else ""


@lru_cache(maxsize=8)
def load_five_letter_words(word_list_path: str | None = None) -> tuple[str, ...]:
    words: set[str] = set()
    if word_list_path:
        with open(word_list_path, encoding="utf-8", errors="ignore") as source:
            words.update(filter(None, (normalize_word(line) for line in source)))
    else:
        for raw_word in top_n_list("en", WORD_LIST_SIZE):
            word = normalize_word(raw_word)
            if word and zipf_frequency(word.lower(), "en") >= MIN_WORD_FREQUENCY:
                words.add(word)
    return tuple(sorted(words))


def find_candidate_words(
    prefix: str, available_letters: str, words: tuple[str, ...] | list[str]
) -> list[str]:
    prefix = normalize_word(prefix) if len(prefix) == 5 else re.sub(r"[^A-Z]", "", prefix.upper())[:5]
    if len(prefix) > 5:
        return []
    available = set(re.sub(r"[^A-Z]", "", available_letters.upper()))
    results = []
    for word in words:
        if not word.startswith(prefix):
            continue
        if len(prefix) == 5 or (available and word[len(prefix)] in available):
            results.append(word)
    return sorted(
        set(results), key=lambda word: (-zipf_frequency(word.lower(), "en"), word)
    )


def save_debug_overlay(image: Image.Image, guess: list[Tile], visible: list[Tile], path: Path) -> None:
    overlay = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    for tile, color, label in [
        *((tile, (255, 120, 0), f"guess:{tile.letter}") for tile in guess),
        *((tile, (0, 200, 0), f"open:{tile.letter}") for tile in visible),
    ]:
        corners = tile.corners.astype(np.int32)
        cv2.polylines(overlay, [corners], True, color, 3)
        cv2.putText(
            overlay,
            label,
            tuple(corners[0]),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), overlay)


def analyze_capture(
    image: Image.Image,
    words: tuple[str, ...],
    debug_path: Path | None = None,
) -> tuple[str, str, list[str]]:
    detected = detect_fully_visible_tiles(image)
    recognized = recognize_tiles(image, detected)
    guess, visible = identify_guess_row(recognized, capture_height=image.height)
    prefix = guess_prefix(guess)
    letters = "".join(tile.letter for tile in visible if tile.letter != "?")
    candidates = find_candidate_words(prefix, letters, words)
    if debug_path:
        save_debug_overlay(image, guess, visible, debug_path)
    return prefix, letters, candidates


def print_result(prefix: str, letters: str, candidates: list[str]) -> None:
    print(f"Guess letters ({len(prefix)}/5): {prefix or '(none)'}")
    print(f"Four-corner-visible letters ({len(letters)}): {letters or '(none)'}")
    print("Possible real five-letter words:")
    if candidates:
        print("  " + "  ".join(candidates))
    else:
        print("  (none)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="capture once instead of continuously")
    parser.add_argument("--interval", type=float, default=CAPTURE_INTERVAL_SECONDS)
    parser.add_argument("--word-list", help="optional text file containing one word per line")
    parser.add_argument("--debug-overlay", type=Path, help="write an annotated image after each scan")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.interval < 0:
        print("--interval cannot be negative", file=sys.stderr)
        return 2
    try:
        words = load_five_letter_words(args.word_list)
        print("Move and resize the guide around the full StackDown stack, including the guess row.")
        region = show_capture_guide(CAPTURE_REGION.copy())
        print(f"Capture region: {region}")
        print("Press Ctrl+C to stop.\n")
        while True:
            started = time.monotonic()
            prefix, letters, candidates = analyze_capture(
                capture_region(region), words, args.debug_overlay
            )
            print_result(prefix, letters, candidates)
            print("-" * 60)
            if args.once:
                break
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nStopped.")
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
