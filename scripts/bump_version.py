#!/usr/bin/env python3
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = ROOT / "VERSION"


def parse_version(raw):
    parts = (raw or "").strip().split(".")
    if len(parts) < 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def main():
    today = datetime.date.today()
    current = None
    if VERSION_PATH.exists():
        current = parse_version(VERSION_PATH.read_text())

    if current and current[0] == today.year and current[1] == today.month:
        patch = current[2] + 1
    else:
        patch = 1

    new_version = f"{today.year}.{today.month:02d}.{patch}"
    VERSION_PATH.write_text(new_version + "\n")
    print(new_version)


if __name__ == "__main__":
    main()
