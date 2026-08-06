# StackDown 5-Letter Word Finder

A screen-based helper that recognizes the current 0–5 letter guess at the
bottom of a StackDown stack, identifies exposed stack letters, and prints real
five-letter English words that can be completed with those letters.

## Detection rules

- The selected rectangle must include the entire stack and all five guess slots.
- The lowest horizontal run of tile-shaped rectangles is the guess row.
- An upper tile is available only when its complete outer contour is detected as
  a convex quadrilateral. This is the computer-vision test for “all four corners
  visible.” Partly covered tiles are deliberately excluded.
- Guess letters are read left-to-right until the first empty/unrecognized slot.
- Candidate words must begin with that guess, and their immediate next letter
  must be one of the currently exposed tiles. Remaining letters are not
  constrained because choosing the next tile changes which stack tiles become
  exposed.
- The built-in dictionary uses common English words from `wordfreq`. Pass a game
  dictionary with `--word-list` if StackDown accepts a different vocabulary.

## Install

Python 3.10+, Tkinter, and Tesseract OCR are required. On macOS with Homebrew:

```bash
brew install python@3.14 python-tk@3.14 tesseract
cd StackDown5LetterWordFinder
"$(brew --prefix python@3.14)/bin/python3.14" -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

On Ubuntu/Debian, install `tesseract-ocr` and `python3-tk` first. On Windows,
install Tesseract and ensure `tesseract.exe` is on `PATH`.

macOS will ask for Screen Recording permission for the terminal or Python app.

## Run

```bash
stackdown-word-finder
```

Move the translucent guide so it covers the full stack plus the bottom five
guess slots. Drag its lower-right corner to resize it, then press Enter.
Recognition refreshes every second; stop with Ctrl+C.

Useful options:

```bash
stackdown-word-finder --once
stackdown-word-finder --interval 2
stackdown-word-finder --word-list /path/to/accepted_words.txt
stackdown-word-finder --debug-overlay detection.png
```

The debug overlay colors the guess row blue and four-corner-visible stack tiles
green. It is the quickest way to tune or diagnose a layout whose tile styling
differs from the expected rectangular StackDown tiles. Detection checks several
brightness and color channels, so colored, rounded, highlighted, or shadowed
tile borders do not need to form a strong grayscale edge.

The detector also segments StackDown's lighter tile-face background as a
second signal. Repeated shapes at the normal tile size are preferred over
large accidental polygons spanning several overlapping tiles.
The especially light cream face used for currently playable tiles is detected
with a separate stricter mask, matching the game's exposed-tile highlight.
Small repeated contours from the printed letter glyphs are rejected before
tile-size clustering.
Shape fallbacks handle the game font's common `Z`→`A`, missing-`P`, round `O`,
narrow `I`, diagonal-stem `N`, and two-bowl `B` OCR errors after a tile has
been identified.
