"""Tests run fully in-memory: no MySQL, no network, no secrets.

Set before app.config is imported, so importing it never trips the
fail-fast check for a missing DB password.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DB_ENABLED", "0")
os.environ.setdefault("OVERLAY_HWACCEL", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
