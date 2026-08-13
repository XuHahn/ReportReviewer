"""Locate a Chrome-compatible executable for evidence-graph PDF export."""

from __future__ import annotations

import os
import platform
import shutil


def find_chrome() -> str:
    configured = os.getenv("CHROME_PATH", "").strip()
    if configured:
        return configured
    system = platform.system()
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Linux":
        candidates = [
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        ]
    elif system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    else:
        candidates = ["google-chrome"]
    return next(
        (candidate for candidate in candidates if shutil.which(candidate) or os.path.isfile(candidate)),
        candidates[0],
    )


CHROME_PATH = find_chrome()
