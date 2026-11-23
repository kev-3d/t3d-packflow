"""Minimal .env loader so local runs (uvicorn without Docker) see the same settings as Compose.

Reads KEY=VALUE lines (optionally only keys with a prefix) from the project-root .env. Variables already set in the
environment win, so Docker/host configuration is never overridden.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ENV = Path(__file__).resolve().parents[2] / ".env"


def load_env(path: Path = PROJECT_ENV, prefix: str = "") -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key.startswith(prefix) and key not in os.environ:
            os.environ[key] = value
