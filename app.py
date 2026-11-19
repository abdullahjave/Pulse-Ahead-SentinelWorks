"""Vercel entrypoint (FastAPI zero-config): exposes the SentinelWorks API as `app`.

The static frontend is served by Vercel's CDN from ./public; every /api/* request lands here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from sentinel.api import app  # noqa: E402,F401
