# AutoClicker

A small, configurable Python auto-clicker. It waits while you position the
pointer, saves that position, and repeatedly left-clicks it until stopped.

## Requirements

- Python 3.9 or newer
- A desktop session supported by [PyAutoGUI](https://pyautogui.readthedocs.io/)

On macOS, allow your terminal or Python application under **System Settings →
Privacy & Security → Accessibility**.

## Installation

From the `assortedTooling` repository:

```bash
cd AutoClicker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

On Windows, activate the environment with `.venv\Scripts\activate` instead.

## Usage

Start with the default eight-second positioning delay and eight-second click
interval:

```bash
autoclicker
```

Customize either delay:

```bash
autoclicker --capture-delay 5 --interval 2
```

You can also run the module directly:

```bash
python auto_clicker.py --help
```

After starting, move the pointer to the location you want clicked. The tool
captures that location after the positioning delay.

## Stopping Safely

- Move the pointer to the top-left corner to trigger PyAutoGUI's failsafe.
- Press `Ctrl+C` in the terminal.
- Send the process `SIGTERM` from a process manager.

Avoid leaving the tool unattended. Confirm the target and interval before using
it in an application where unintended clicks could cause changes.
