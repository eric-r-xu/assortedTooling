#!/usr/bin/env python3
"""Find high-scoring Lettergrams boards using every tile.

The solver builds connected crossword-style layouts on a square board.  Every
maximal horizontal or vertical run of two or more letters must occur in the
chosen dictionary, and every tile must belong to at least one such word.
"""

from __future__ import annotations

import argparse
import heapq
import itertools
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    tk = None  # type: ignore
    ttk = None  # type: ignore

LETTER_VALUES = {
    "A": 1, "B": 3, "C": 3, "D": 2, "E": 1, "F": 4, "G": 2,
    "H": 4, "I": 1, "J": 8, "K": 5, "L": 1, "M": 3, "N": 1,
    "O": 1, "P": 3, "Q": 10, "R": 1, "S": 1, "T": 1, "U": 1,
    "V": 4, "W": 4, "X": 8, "Y": 4, "Z": 10,
}

DEFAULT_BONUSES = {(0, 1): "DW", (2, 1): "TL", (2, 4): "DL", (4, 3): "TW"}
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DICTIONARY_PATH = SCRIPT_DIR / "dictionary.txt"

COMMON_DICTIONARIES = (
    DEFAULT_DICTIONARY_PATH,
    "scrabble_words.txt",
    "dictionary.txt",
    "words.txt",
    "/usr/share/dict/words",
)


@dataclass(frozen=True)
class Result:
    score: int
    board: tuple[str, ...]
    words: tuple[tuple[str, int, str, int, int], ...]


def normalize_letters(value: str) -> str:
    letters = re.sub(r"[^A-Za-z]", "", value).upper()
    if not letters:
        raise argparse.ArgumentTypeError("letters must contain A-Z")
    return letters


def find_dictionary(requested: str | None) -> Path:
    candidates = [requested] if requested else list(COMMON_DICTIONARIES)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    if requested:
        raise FileNotFoundError(f"dictionary not found: {requested}")
    raise FileNotFoundError(
        "no dictionary found; pass --dictionary PATH (an NWL or CSW word list is recommended)"
    )


def load_words(path: Path, rack: Counter[str], size: int) -> tuple[set[str], list[str]]:
    all_words: set[str] = set()
    candidates: list[str] = []
    with path.open(encoding="utf-8", errors="ignore") as source:
        for raw in source:
            word = raw.strip().upper()
            if not (2 <= len(word) <= size and word.isascii() and word.isalpha()):
                continue
            all_words.add(word)
            if not (Counter(word) - rack):
                candidates.append(word)
    candidates = sorted(set(candidates), key=lambda w: (-len(w), w))
    return all_words, candidates


def parse_bonus(value: str, size: int) -> tuple[tuple[int, int], str]:
    match = re.fullmatch(r"(DL|TL|DW|TW)@(\d+),(\d+)", value.upper())
    if not match:
        raise argparse.ArgumentTypeError("bonus must look like TL@3,2 (rows/columns are 1-based)")
    kind, row, col = match.group(1), int(match.group(2)), int(match.group(3))
    if not (1 <= row <= size and 1 <= col <= size):
        raise argparse.ArgumentTypeError(f"bonus position must be within a {size}x{size} board")
    return (row - 1, col - 1), kind


def runs(board: Sequence[str], size: int) -> Iterator[tuple[str, tuple[int, ...], str, int, int]]:
    for direction in ("across", "down"):
        for major in range(size):
            letters: list[str] = []
            cells: list[int] = []
            start = 0
            for minor in range(size + 1):
                index = major * size + minor if direction == "across" else minor * size + major
                letter = board[index] if minor < size else ""
                if letter:
                    if not letters:
                        start = minor
                    letters.append(letter)
                    cells.append(index)
                else:
                    if len(letters) >= 2:
                        yield "".join(letters), tuple(cells), direction, major, start
                    letters, cells = [], []


def score_board(
    board: Sequence[str], size: int, bonuses: dict[tuple[int, int], str], dictionary: set[str]
) -> Result | None:
    found: list[tuple[str, int, str, int, int]] = []
    covered: set[int] = set()
    total = 0
    for word, cells, direction, major, start in runs(board, size):
        if word not in dictionary:
            return None
        subtotal = 0
        word_multiplier = 1
        for index in cells:
            row, col = divmod(index, size)
            value = LETTER_VALUES[board[index]]
            bonus = bonuses.get((row, col), "")
            if bonus == "DL":
                value *= 2
            elif bonus == "TL":
                value *= 3
            elif bonus == "DW":
                word_multiplier = max(word_multiplier, 2)
            elif bonus == "TW":
                word_multiplier = max(word_multiplier, 3)
            subtotal += value
        points = subtotal * word_multiplier
        total += points
        row, col = (major, start) if direction == "across" else (start, major)
        found.append((word, points, direction, row + 1, col + 1))
        covered.update(cells)
    occupied = {i for i, letter in enumerate(board) if letter}
    if occupied != covered:
        return None
    return Result(total, tuple(board), tuple(found))


def partial_priority(
    board: tuple[str, ...], size: int, bonuses: dict[tuple[int, int], str], dictionary: set[str]
) -> int:
    """A ranking heuristic; it is deliberately not used as an exact bound."""
    valid_score = 0
    crossing_cells: Counter[int] = Counter()
    for word, cells, _direction, _major, _start in runs(board, size):
        if word in dictionary:
            result = score_word(word, cells, board, size, bonuses)
            valid_score += result
            crossing_cells.update(cells)
    used = sum(bool(letter) for letter in board)
    crossings = sum(count > 1 for count in crossing_cells.values())
    occupied_bonus = sum(
        1 for (row, col) in bonuses if board[row * size + col]
    )
    return valid_score * 10 + used * 8 + crossings * 15 + occupied_bonus * 8


def score_word(
    word: str,
    cells: Sequence[int],
    board: Sequence[str],
    size: int,
    bonuses: dict[tuple[int, int], str],
) -> int:
    subtotal = 0
    multiplier = 1
    for index in cells:
        row, col = divmod(index, size)
        bonus = bonuses.get((row, col), "")
        value = LETTER_VALUES[board[index]]
        if bonus == "DL":
            value *= 2
        elif bonus == "TL":
            value *= 3
        elif bonus == "DW":
            multiplier = max(multiplier, 2)
        elif bonus == "TW":
            multiplier = max(multiplier, 3)
        subtotal += value
    return subtotal * multiplier


def placed_board(
    board: tuple[str, ...], word: str, row: int, col: int, direction: int, size: int,
    rack: Counter[str], tile_count: int,
) -> tuple[str, ...] | None:
    step = 1 if direction == 0 else size
    start = row * size + col
    updated = list(board)
    overlap = False
    added: Counter[str] = Counter()
    for offset, letter in enumerate(word):
        index = start + offset * step
        existing = updated[index]
        if existing and existing != letter:
            return None
        if existing:
            overlap = True
        else:
            updated[index] = letter
            added[letter] += 1
    if any(added[letter] > rack[letter] - board.count(letter) for letter in added):
        return None
    if sum(bool(letter) for letter in updated) > tile_count:
        return None
    if any(board) and not overlap:
        return None
    if not added:
        return None
    return tuple(updated)


def placements_for_word(word: str, size: int) -> Iterator[tuple[int, int, int]]:
    for direction in (0, 1):
        row_limit = size if direction == 0 else size - len(word) + 1
        col_limit = size - len(word) + 1 if direction == 0 else size
        for row in range(row_limit):
            for col in range(col_limit):
                yield row, col, direction


def solve(
    letters: str,
    size: int,
    bonuses: dict[tuple[int, int], str],
    dictionary: set[str],
    candidates: list[str],
    beam_width: int,
    time_limit: float,
    result_count: int,
    exhaustive: bool,
    log_interval: float = 5.0,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[Result], int, int]:
    rack = Counter(letters)
    empty = tuple("" for _ in range(size * size))
    by_letter: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for word in candidates:
        for offset, letter in enumerate(word):
            by_letter[letter].append((word, offset))

    serial = itertools.count()
    frontier: list[tuple[int, int, tuple[str, ...]]] = []
    seen = {empty}
    for word in candidates:
        for row, col, direction in placements_for_word(word, size):
            board = placed_board(empty, word, row, col, direction, size, rack, len(letters))
            if board is not None and board not in seen:
                seen.add(board)
                priority = partial_priority(board, size, bonuses, dictionary)
                heapq.heappush(frontier, (-priority, next(serial), board))

    deadline = float("inf") if time_limit <= 0 else time.monotonic() + time_limit
    results: dict[tuple[str, ...], Result] = {}
    expanded = 0
    per_used: Counter[int] = Counter()
    last_log_time = time.monotonic()

    while frontier and time.monotonic() < deadline:
        now = time.monotonic()
        if log_interval > 0 and now - last_log_time >= log_interval:
            print(f"Thinking... ({expanded:,} states searched)", flush=True)
            if progress_callback:
                progress_callback(expanded, len(seen))
            last_log_time = now

        _negative_priority, _serial, board = heapq.heappop(frontier)
        used = sum(bool(letter) for letter in board)
        if not exhaustive and per_used[used] >= beam_width:
            continue
        per_used[used] += 1
        expanded += 1
        if used == len(letters):
            result = score_board(board, size, bonuses, dictionary)
            if result is not None:
                results[board] = result
            continue

        occupied = [(index, letter) for index, letter in enumerate(board) if letter]
        generated: set[tuple[str, int, int, int]] = set()
        for index, letter in occupied:
            board_row, board_col = divmod(index, size)
            for word, offset in by_letter[letter]:
                for direction in (0, 1):
                    row = board_row if direction == 0 else board_row - offset
                    col = board_col - offset if direction == 0 else board_col
                    if row < 0 or col < 0:
                        continue
                    if direction == 0 and col + len(word) > size:
                        continue
                    if direction == 1 and row + len(word) > size:
                        continue
                    key = word, row, col, direction
                    if key in generated:
                        continue
                    generated.add(key)
                    new_board = placed_board(
                        board, word, row, col, direction, size, rack, len(letters)
                    )
                    if new_board is None or new_board in seen:
                        continue
                    seen.add(new_board)
                    priority = partial_priority(new_board, size, bonuses, dictionary)
                    heapq.heappush(frontier, (-priority, next(serial), new_board))

    ordered = sorted(results.values(), key=lambda result: (-result.score, result.board))
    return ordered[:result_count], expanded, len(seen)


def format_board(board: Sequence[str], size: int, bonuses: dict[tuple[int, int], str]) -> str:
    rows = []
    for row in range(size):
        cells = []
        for col in range(size):
            letter = board[row * size + col]
            cells.append(f" {letter} " if letter else f"{bonuses.get((row, col), '.'):^3}")
        rows.append("|".join(cells))
    return "\n".join(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--letters", type=normalize_letters, required=False, default=None,
        help="rack letters (required in CLI mode; punctuation and spaces are ignored)",
    )
    parser.add_argument("--dictionary", help="NWL/CSW/plain word-list file, one word per line (default: dictionary.txt)")
    parser.add_argument("--size", type=int, default=5, help="square board size (default: 5)")
    parser.add_argument("--bonus", action="append", default=None,
                        help="bonus such as DW@1,2; repeat for each bonus")
    parser.add_argument("--beam-width", type=int, default=3000,
                        help="states expanded per tile count (default: 3000)")
    parser.add_argument("--time-limit", type=float, default=30.0,
                        help="search seconds; 0 means unlimited (default: 30)")
    parser.add_argument("--results", type=int, default=5, help="number of boards to show")
    parser.add_argument("--exhaustive", action="store_true",
                        help="disable beam pruning (can take a very long time)")
    parser.add_argument(
        "--log-interval", type=float, default=5.0,
        help="seconds between thinking status updates (default: 5.0; 0 to disable)",
    )
    parser.add_argument("--gui", action="store_true", help="launch graphical user interface")
    return parser


BONUS_TYPES = ["", "DL", "TL", "DW", "TW"]
BONUS_STYLES = {
    "": {"bg": "#F5F5F5", "fg": "#333333", "text": "."},
    "DL": {"bg": "#E0F7FA", "fg": "#006064", "text": "DL"},
    "TL": {"bg": "#E8EAF6", "fg": "#1A237E", "text": "TL"},
    "DW": {"bg": "#FCE4EC", "fg": "#880E4F", "text": "DW"},
    "TW": {"bg": "#FFEBEE", "fg": "#B71C1C", "text": "TW"},
}


class LetterGramsGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LetterGrams Board Solver")
        self.root.geometry("820x860")
        self.root.minsize(700, 750)

        # Style setup
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        # Variables
        self.letters_var = tk.StringVar(value="ZYNUGILDOATR")
        self.size_var = tk.IntVar(value=5)
        self.dw_var = tk.StringVar(value="1,2")
        self.tl_var = tk.StringVar(value="3,2")
        self.dl_var = tk.StringVar(value="3,5")
        self.tw_var = tk.StringVar(value="5,4")
        self.time_limit_var = tk.StringVar(value="30.0")
        self.beam_width_var = tk.StringVar(value="3000")
        self.results_var = tk.StringVar(value="5")
        self.exhaustive_var = tk.BooleanVar(value=False)
        self.dictionary_var = tk.StringVar(value=str(DEFAULT_DICTIONARY_PATH))
        self.status_var = tk.StringVar(value="Ready")

        self.grid_buttons: dict[tuple[int, int], tk.Button] = {}
        self.cell_bonuses: dict[tuple[int, int], str] = {}
        self.is_solving = False

        self._build_ui()
        self._sync_text_to_grid()

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding="12")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 8))

        header_label = ttk.Label(
            header_frame, text="LetterGrams Solver", font=("Helvetica", 18, "bold")
        )
        header_label.pack(anchor=tk.W)
        sub_label = ttk.Label(
            header_frame,
            text="Enter rack letters and configure bonus tile positions (DW, TL, DL, TW):",
            font=("Helvetica", 11),
        )
        sub_label.pack(anchor=tk.W, pady=(2, 0))

        # Inputs section split into Left (Settings & Text Bonuses) and Right (Interactive Grid)
        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.X, expand=False, pady=(0, 10))

        left_frame = ttk.LabelFrame(content_frame, text=" Inputs & Bonus Positions ", padding="10")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        right_frame = ttk.LabelFrame(content_frame, text=" 5x5 Board Interactive Grid ", padding="10")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False)

        # --- Left Frame Items ---
        # Rack letters
        ttk.Label(left_frame, text="Rack Letters:", font=("Helvetica", 11, "bold")).grid(row=0, column=0, sticky=tk.W, pady=4)
        letters_entry = ttk.Entry(left_frame, textvariable=self.letters_var, font=("Helvetica", 13, "bold"), width=24)
        letters_entry.grid(row=0, column=1, sticky=tk.W, pady=4, padx=(5, 0))

        # Dictionary
        ttk.Label(left_frame, text="Dictionary:").grid(row=1, column=0, sticky=tk.W, pady=4)
        dict_frame = ttk.Frame(left_frame)
        dict_frame.grid(row=1, column=1, sticky=tk.W, pady=4, padx=(5, 0))
        dict_entry = ttk.Entry(dict_frame, textvariable=self.dictionary_var, width=18)
        dict_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dict_frame, text="Browse", width=7, command=self._browse_dict).pack(side=tk.LEFT, padx=(4, 0))

        # Bonus position text inputs
        ttk.Label(left_frame, text="DW Positions (r,c):").grid(row=2, column=0, sticky=tk.W, pady=2)
        dw_entry = ttk.Entry(left_frame, textvariable=self.dw_var, width=16)
        dw_entry.grid(row=2, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        dw_entry.bind("<FocusOut>", lambda e: self._sync_text_to_grid())
        dw_entry.bind("<Return>", lambda e: self._sync_text_to_grid())

        ttk.Label(left_frame, text="TL Positions (r,c):").grid(row=3, column=0, sticky=tk.W, pady=2)
        tl_entry = ttk.Entry(left_frame, textvariable=self.tl_var, width=16)
        tl_entry.grid(row=3, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        tl_entry.bind("<FocusOut>", lambda e: self._sync_text_to_grid())
        tl_entry.bind("<Return>", lambda e: self._sync_text_to_grid())

        ttk.Label(left_frame, text="DL Positions (r,c):").grid(row=4, column=0, sticky=tk.W, pady=2)
        dl_entry = ttk.Entry(left_frame, textvariable=self.dl_var, width=16)
        dl_entry.grid(row=4, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        dl_entry.bind("<FocusOut>", lambda e: self._sync_text_to_grid())
        dl_entry.bind("<Return>", lambda e: self._sync_text_to_grid())

        ttk.Label(left_frame, text="TW Positions (r,c):").grid(row=5, column=0, sticky=tk.W, pady=2)
        tw_entry = ttk.Entry(left_frame, textvariable=self.tw_var, width=16)
        tw_entry.grid(row=5, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        tw_entry.bind("<FocusOut>", lambda e: self._sync_text_to_grid())
        tw_entry.bind("<Return>", lambda e: self._sync_text_to_grid())

        # Options
        opts_frame = ttk.Frame(left_frame)
        opts_frame.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))

        ttk.Label(opts_frame, text="Time Limit (s):").grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        ttk.Entry(opts_frame, textvariable=self.time_limit_var, width=6).grid(row=0, column=1, sticky=tk.W, padx=(0, 10))

        ttk.Label(opts_frame, text="Beam Width:").grid(row=0, column=2, sticky=tk.W, padx=(0, 4))
        ttk.Entry(opts_frame, textvariable=self.beam_width_var, width=6).grid(row=0, column=3, sticky=tk.W)

        ttk.Checkbutton(opts_frame, text="Exhaustive Search", variable=self.exhaustive_var).grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=(4, 0))

        # --- Right Frame: Interactive Grid ---
        grid_container = ttk.Frame(right_frame)
        grid_container.pack()

        legend_label = ttk.Label(
            right_frame,
            text="Click grid cells to cycle bonus type:\n. ➔ DL ➔ TL ➔ DW ➔ TW",
            font=("Helvetica", 9),
            justify=tk.CENTER,
        )
        legend_label.pack(pady=(4, 0))

        for r in range(5):
            for c in range(5):
                btn = tk.Button(
                    grid_container,
                    text=".",
                    width=4,
                    height=2,
                    font=("Helvetica", 10, "bold"),
                    command=lambda row=r, col=c: self._on_grid_click(row, col),
                )
                btn.grid(row=r, column=c, padx=2, pady=2)
                self.grid_buttons[(r, c)] = btn
                self.cell_bonuses[(r, c)] = ""

        # --- Action & Status Bar ---
        action_frame = ttk.Frame(main_frame)
        action_frame.pack(fill=tk.X, pady=(0, 8))

        self.solve_btn = tk.Button(
            action_frame,
            text="⚡ Solve LetterGrams",
            font=("Helvetica", 12, "bold"),
            bg="#2E7D32",
            fg="white",
            activebackground="#1B5E20",
            activeforeground="white",
            padx=14,
            pady=5,
            command=self._start_solve,
        )
        self.solve_btn.pack(side=tk.LEFT, padx=(0, 12))

        status_label = ttk.Label(
            action_frame,
            textvariable=self.status_var,
            font=("Helvetica", 11, "italic"),
            foreground="#1565C0",
        )
        status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # --- Results Output Section ---
        results_frame = ttk.LabelFrame(main_frame, text=" Solver Output & Results ", padding="8")
        results_frame.pack(fill=tk.BOTH, expand=True)

        self.results_text = tk.Text(
            results_frame,
            wrap=tk.WORD,
            font=("Courier", 12),
            bg="#1E1E1E",
            fg="#F0F0F0",
            insertbackground="white",
        )
        scrollbar = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=self.results_text.yview)
        self.results_text.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.results_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _browse_dict(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select Dictionary File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.dictionary_var.set(path)

    def _on_grid_click(self, r: int, c: int):
        current = self.cell_bonuses.get((r, c), "")
        idx = (BONUS_TYPES.index(current) + 1) % len(BONUS_TYPES)
        nxt = BONUS_TYPES[idx]
        self.cell_bonuses[(r, c)] = nxt
        self._update_button_style(r, c)
        self._sync_grid_to_text()

    def _update_button_style(self, r: int, c: int):
        bonus = self.cell_bonuses.get((r, c), "")
        cfg = BONUS_STYLES.get(bonus, BONUS_STYLES[""])
        btn = self.grid_buttons[(r, c)]
        btn.config(bg=cfg["bg"], fg=cfg["fg"], text=cfg["text"])

    def _sync_grid_to_text(self):
        dw, tl, dl, tw = [], [], [], []
        for (r, c), bonus in sorted(self.cell_bonuses.items()):
            pos = f"{r+1},{c+1}"
            if bonus == "DW":
                dw.append(pos)
            elif bonus == "TL":
                tl.append(pos)
            elif bonus == "DL":
                dl.append(pos)
            elif bonus == "TW":
                tw.append(pos)
        self.dw_var.set(" ".join(dw))
        self.tl_var.set(" ".join(tl))
        self.dl_var.set(" ".join(dl))
        self.tw_var.set(" ".join(tw))

    def _sync_text_to_grid(self):
        self.cell_bonuses = {(r, c): "" for r in range(5) for c in range(5)}
        def parse_coords(text: str) -> list[tuple[int, int]]:
            res = []
            matches = re.findall(r"(\d+)\s*,\s*(\d+)", text)
            for r_s, c_s in matches:
                r, c = int(r_s) - 1, int(c_s) - 1
                if 0 <= r < 5 and 0 <= c < 5:
                    res.append((r, c))
            return res

        for r, c in parse_coords(self.dw_var.get()):
            self.cell_bonuses[(r, c)] = "DW"
        for r, c in parse_coords(self.tl_var.get()):
            self.cell_bonuses[(r, c)] = "TL"
        for r, c in parse_coords(self.dl_var.get()):
            self.cell_bonuses[(r, c)] = "DL"
        for r, c in parse_coords(self.tw_var.get()):
            self.cell_bonuses[(r, c)] = "TW"

        for (r, c) in self.grid_buttons:
            self._update_button_style(r, c)

    def _start_solve(self):
        if self.is_solving:
            return
        from tkinter import messagebox

        raw_letters = self.letters_var.get()
        try:
            letters = normalize_letters(raw_letters)
        except argparse.ArgumentTypeError:
            messagebox.showerror("Invalid Input", "Rack letters must contain at least one letter (A-Z).")
            return

        dict_path_str = self.dictionary_var.get().strip()
        try:
            dict_path = find_dictionary(dict_path_str or None)
        except Exception as err:
            messagebox.showerror("Dictionary Error", str(err))
            return

        self._sync_text_to_grid()
        self.is_solving = True
        self.solve_btn.config(state=tk.DISABLED)
        self.status_var.set("Thinking... starting solver...")
        self.results_text.delete("1.0", tk.END)

        import threading
        threading.Thread(target=self._run_solver_thread, args=(letters, dict_path), daemon=True).start()

    def _run_solver_thread(self, letters: str, dict_path: Path):
        try:
            size = self.size_var.get()
            time_limit = float(self.time_limit_var.get() or 30.0)
            beam_width = int(self.beam_width_var.get() or 3000)
            result_count = int(self.results_var.get() or 5)
            exhaustive = self.exhaustive_var.get()
            bonuses = {k: v for k, v in self.cell_bonuses.items() if v}

            self.root.after(0, lambda: self._append_text(f"Dictionary: {dict_path}\nRack: {letters}\nSearching for solutions...\n\n"))

            dictionary, candidates = load_words(dict_path, Counter(letters), size)
            if not candidates:
                self.root.after(0, lambda: self._finish_solve("No words constructible from these tiles.", "No candidates found in dictionary for these tiles.\n"))
                return

            def progress_cb(expanded: int, seen: int):
                msg = f"Thinking... ({expanded:,} states searched)"
                self.root.after(0, lambda: self.status_var.set(msg))

            results, expanded, seen = solve(
                letters, size, bonuses, dictionary, candidates,
                beam_width, time_limit, result_count, exhaustive,
                log_interval=5.0, progress_callback=progress_cb,
            )

            out = [f"Searched {expanded:,} states ({seen:,} unique boards).\n"]
            if not results:
                out.append("No complete board found. Try a larger beam-width or time-limit.\n")
            else:
                for number, result in enumerate(results, 1):
                    out.append(f"#{number}: {result.score} points")
                    out.append(format_board(result.board, size, bonuses))
                    for word, points, direction, row, col in result.words:
                        out.append(f"  {word:<5} {points:>3}  {direction} from ({row},{col})")
                    out.append("")

            res_str = "\n".join(out)
            self.root.after(0, lambda: self._finish_solve(f"Done! Found {len(results)} solution(s).", res_str))
        except Exception as exc:
            err_str = f"Error: {exc}"
            self.root.after(0, lambda: self._finish_solve(err_str, f"{err_str}\n"))

    def _append_text(self, text: str):
        self.results_text.insert(tk.END, text)
        self.results_text.see(tk.END)

    def _finish_solve(self, status_msg: str, output_text: str):
        self.status_var.set(status_msg)
        if output_text:
            self._append_text(output_text)
        self.solve_btn.config(state=tk.NORMAL)
        self.is_solving = False


def launch_gui() -> int:
    if tk is None or ttk is None:
        print("Error: Tkinter is required for GUI mode but is not available on this system.", file=sys.stderr)
        return 1

    root = tk.Tk()
    _app = LetterGramsGUI(root)
    root.mainloop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    import sys

    if argv is None:
        argv = sys.argv[1:]

    if not argv or "--gui" in argv:
        return launch_gui()

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.letters:
        parser.error("--letters is required in CLI mode (or run without arguments to launch GUI)")
    if args.size < 2:
        parser.error("--size must be at least 2")
    letters = args.letters
    if len(letters) > args.size * args.size:
        parser.error("there are more tiles than board spaces")
    if args.beam_width < 1 or args.results < 1:
        parser.error("--beam-width and --results must be positive")

    if args.bonus is None:
        bonuses = DEFAULT_BONUSES.copy() if args.size == 5 else {}
    else:
        bonuses = dict(parse_bonus(value, args.size) for value in args.bonus)

    try:
        dictionary_path = find_dictionary(args.dictionary)
        dictionary, candidates = load_words(dictionary_path, Counter(letters), args.size)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if not candidates:
        parser.error("the dictionary contains no words constructible from these tiles")

    print(f"Dictionary: {dictionary_path} ({len(dictionary):,} words of length 2-{args.size})")
    print(f"Rack: {letters} | candidate words: {len(candidates):,}")
    if dictionary_path == Path("/usr/share/dict/words"):
        print("Note: the system dictionary is not an official Scrabble lexicon; use --dictionary NWL.txt or CSW.txt for exact validation.")
    results, expanded, seen = solve(
        letters, args.size, bonuses, dictionary, candidates,
        args.beam_width, args.time_limit, args.results, args.exhaustive,
        args.log_interval,
    )
    print(f"Searched {expanded:,} states ({seen:,} unique boards).")
    if not results:
        print("No complete board found. Try a larger --beam-width/--time-limit or another dictionary.")
        return 1
    for number, result in enumerate(results, 1):
        print(f"\n#{number}: {result.score} points")
        print(format_board(result.board, args.size, bonuses))
        for word, points, direction, row, col in result.words:
            print(f"  {word:<5} {points:>3}  {direction} from ({row},{col})")
    if not args.exhaustive:
        print("\nBest found by bounded search; increase --beam-width/--time-limit for a stronger search.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
