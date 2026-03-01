#!/usr/bin/env python3
"""Sync local skill audit JSON entries into Convex."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

try:
    from convex import ConvexClient
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: convex. Install with `pip install convex`."
    ) from exc


def load_entries(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Expected a top-level JSON array.")
    return [row for row in data if isinstance(row, dict)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync skill_audit_report.json into Convex table skillAudits."
    )
    parser.add_argument(
        "--input",
        default="skill_audit_report.json",
        help="Path to JSON report (default: skill_audit_report.json)",
    )
    parser.add_argument(
        "--convex-url",
        default=os.getenv("CONVEX_URL", ""),
        help="Convex deployment URL (or set CONVEX_URL)",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.0,
        help="Optional sleep between upserts in seconds",
    )
    parser.add_argument(
        "--clear-first",
        action="store_true",
        help="Delete all existing skillAudits rows before syncing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.convex_url:
        print("Missing --convex-url (or CONVEX_URL env var).", file=sys.stderr)
        return 2

    try:
        entries = load_entries(args.input)
    except (OSError, json.JSONDecodeError, ValueError) as err:
        print(f"Failed to read {args.input}: {err}", file=sys.stderr)
        return 1

    client = ConvexClient(args.convex_url)
    print(f"Loaded {len(entries)} entries from {args.input}")
    print(f"Target Convex deployment: {args.convex_url}")

    if args.clear_first:
        result = client.mutation("skillAudits:clearAll", {})
        print(f"Cleared existing rows: {result.get('deleted', 0)}")

    ok = 0
    failed = 0
    for idx, entry in enumerate(entries, start=1):
        slug = entry.get("slug", "unknown-skill")
        try:
            client.mutation("skillAudits:upsert", {"entry": entry})
            ok += 1
        except Exception as err:  # pragma: no cover
            failed += 1
            print(f"[{idx}/{len(entries)}] FAIL {slug}: {err}", file=sys.stderr)
            continue

        print(f"[{idx}/{len(entries)}] OK   {slug}")
        if args.pause > 0:
            time.sleep(args.pause)

    print(f"Sync complete. success={ok} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
