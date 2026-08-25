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
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

LETTER_VALUES = {
    "A": 1, "B": 3, "C": 3, "D": 2, "E": 1, "F": 4, "G": 2,
    "H": 4, "I": 1, "J": 8, "K": 5, "L": 1, "M": 3, "N": 1,
    "O": 1, "P": 3, "Q": 10, "R": 1, "S": 1, "T": 1, "U": 1,
    "V": 4, "W": 4, "X": 8, "Y": 4, "Z": 10,
}

DEFAULT_BONUSES = {(0, 1): "DW", (2, 1): "TL", (2, 4): "DL", (4, 3): "TW"}
COMMON_DICTIONARIES = (
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

    while frontier and time.monotonic() < deadline:
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
        "--letters", type=normalize_letters, required=True,
        help="rack letters (required; punctuation and spaces are ignored)",
    )
    parser.add_argument("--dictionary", help="NWL/CSW/plain word-list file, one word per line")
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
