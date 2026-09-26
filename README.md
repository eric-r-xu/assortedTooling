# Assorted Tooling

A small collection of independent Python desktop-automation and
computer-vision tools. Each tool has its own dependencies and documentation so
it can be installed and run without coupling it to the others.

## Tools

| Tool | Description | Start here |
| --- | --- | --- |
| [OneWordSearchAutoPlay](OneWordSearchAutoPlay/) | Recognizes a 5×5 OneWordSearch board and optionally traces a valid word. | [README](OneWordSearchAutoPlay/README.md) |
| [WordBlitzAutoPlay](WordBlitzAutoPlay/) | Solves connected words on a 4×4 Word Blitz-style board and optionally traces them. | [README](WordBlitzAutoPlay/README.md) |
| [LetterGrams](LetterGrams/) | Searches for connected 5×5 crossword layouts using a rack of letters and word dictionaries with bonus square support. | [README](LetterGrams/README.md) |
| [AutoClicker](AutoClicker/) | Saves the current pointer position and repeatedly clicks it at a configurable interval. | [README](AutoClicker/README.md) |
| [SMS & WeChat Scheduler](smsScheduler/) | Schedules one-time Messages or WeChat messages on macOS. Includes an Apple Silicon DMG. | [Download & guide](smsScheduler/README.md) |

## Getting Started

Clone the monorepo:

```bash
git clone https://github.com/eric-r-xu/assortedTooling.git
cd assortedTooling
```

Then open the README for the tool you want to use. **SMS & WeChat Scheduler**
includes a [ready-to-install Mac app](smsScheduler/README.md#install-in-a-minute)
and does not require Python setup when using the DMG. For tools run from source,
create a separate virtual
environment inside that tool's directory; the tools intentionally do not share
one environment because their dependencies and system requirements differ.

For example:

```bash
cd AutoClicker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
autoclicker --help
```

## Safety and Permissions

Some tools can capture the screen and control the mouse. For tools using
PyAutoGUI, the corner failsafe stops automated mouse input when the pointer
moves to the top-left corner. Terminal tools can also be stopped with `Ctrl+C`.
SMS & WeChat Scheduler uses macOS background jobs and separate automation;
cancel pending messages in its window before uninstalling or stopping schedules.

On macOS, your terminal or Python application may need Accessibility and Screen
Recording access under **System Settings → Privacy & Security**.

Use these tools only where automation is permitted and review each tool's
configuration before enabling automatic input.

## Repository Layout

```text
assortedTooling/
├── AutoClicker/
├── LetterGrams/
├── OneWordSearchAutoPlay/
├── WordBlitzAutoPlay/
└── smsScheduler/
```

Generated files, virtual environments, local OCR templates, and build artifacts
are excluded from version control, except for the intentionally checked-in
scheduler release DMG and checksum under `smsScheduler/downloads/`.
