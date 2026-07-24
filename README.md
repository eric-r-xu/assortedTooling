# Assorted Tooling

A small collection of independent Python desktop-automation and
computer-vision tools. Each tool has its own dependencies and documentation so
it can be installed and run without coupling it to the others.

## Tools

| Tool | Description | Start here |
| --- | --- | --- |
| [OneWordSearchAutoPlay](OneWordSearchAutoPlay/) | Recognizes a 5×5 OneWordSearch board and optionally traces a valid word. | [README](OneWordSearchAutoPlay/README.md) |
| [WordBlitzAutoPlay](WordBlitzAutoPlay/) | Solves connected words on a 4×4 Word Blitz-style board and optionally traces them. | [README](WordBlitzAutoPlay/README.md) |
| [AutoClicker](AutoClicker/) | Saves the current pointer position and repeatedly clicks it at a configurable interval. | [README](AutoClicker/README.md) |

## Getting Started

Clone the monorepo:

```bash
git clone https://github.com/eric-r-xu/assortedTooling.git
cd assortedTooling
```

Then open the README for the tool you want to use. Create a separate virtual
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

These tools can capture the screen and control the mouse. PyAutoGUI's corner
failsafe is enabled: moving the pointer to the top-left corner stops automated
mouse input. You can also stop a running tool with `Ctrl+C`.

On macOS, your terminal or Python application may need Accessibility and Screen
Recording access under **System Settings → Privacy & Security**.

Use these tools only where automation is permitted and review each tool's
configuration before enabling automatic input.

## Repository Layout

```text
assortedTooling/
├── AutoClicker/
├── OneWordSearchAutoPlay/
└── WordBlitzAutoPlay/
```

Generated files, virtual environments, local OCR templates, and build artifacts
are excluded from version control.
