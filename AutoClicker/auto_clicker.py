#!/usr/bin/env python3

import argparse
import signal
import sys
import time
from collections.abc import Sequence
from typing import Optional

import pyautogui


DEFAULT_CAPTURE_DELAY_SECONDS = 8.0
DEFAULT_CLICK_INTERVAL_SECONDS = 8.0

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1

running = True


def positive_seconds(value: str) -> float:
    """Parse a positive number of seconds for a command-line option."""
    seconds = float(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture the current pointer position after a delay, then click it "
            "repeatedly until stopped."
        )
    )
    parser.add_argument(
        "--capture-delay",
        type=positive_seconds,
        default=DEFAULT_CAPTURE_DELAY_SECONDS,
        metavar="SECONDS",
        help=(
            "time available to position the pointer before its location is "
            f"saved (default: {DEFAULT_CAPTURE_DELAY_SECONDS:g})"
        ),
    )
    parser.add_argument(
        "--interval",
        type=positive_seconds,
        default=DEFAULT_CLICK_INTERVAL_SECONDS,
        metavar="SECONDS",
        help=(
            "time between clicks "
            f"(default: {DEFAULT_CLICK_INTERVAL_SECONDS:g})"
        ),
    )
    return parser.parse_args(argv)


def stop_script(
    _signum: Optional[int] = None,
    _frame: Optional[object] = None,
) -> None:
    """Request a clean stop after the current wait or click."""
    global running
    running = False


def wait_until_stopped(seconds: float) -> None:
    """Wait for up to ``seconds`` while remaining responsive to signals."""
    deadline = time.monotonic() + seconds
    while running:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.1))


def main(argv: Optional[Sequence[str]] = None) -> int:
    global running
    running = True
    args = parse_args(argv)

    signal.signal(signal.SIGINT, stop_script)
    signal.signal(signal.SIGTERM, stop_script)

    print(
        "Move your mouse to the desired click location. "
        f"The position will be saved in {args.capture_delay:g} seconds."
    )

    wait_until_stopped(args.capture_delay)
    if not running:
        print("Clicker stopped before a position was saved.")
        return 0

    target = pyautogui.position()
    target_x = target.x
    target_y = target.y

    print(f"Saved click position: x={target_x}, y={target_y}")
    print(
        f"Clicking every {args.interval:g} seconds. "
        "Move the mouse to the top-left corner to stop."
    )

    try:
        while running:
            pyautogui.click(target_x, target_y, button="left")
            print(f"Clicked x={target_x}, y={target_y}", flush=True)

            wait_until_stopped(args.interval)

    except pyautogui.FailSafeException:
        print("Emergency stop triggered.")
        return 0
    except Exception as exc:
        print(f"Clicker stopped because of an error: {exc}", file=sys.stderr)
        return 1

    print("Clicker stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
