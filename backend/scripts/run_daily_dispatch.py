#!/usr/bin/env python
"""
Standalone execution script for running AI News Daily Dispatch.
Compatible with cron, GitHub Actions, systemd timers, or cloud workers.

Usage:
  python backend/scripts/run_daily_dispatch.py [--dry-run] [--force] [--no-ingest] [--date YYYY-MM-DD]
"""
import sys
from pathlib import Path

# Ensure backend root is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
from app.services.dispatch_service import _run_cli

if __name__ == "__main__":
    asyncio.run(_run_cli())
