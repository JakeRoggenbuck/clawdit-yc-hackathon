#!/usr/bin/env python3
"""Fetch one skills list page from ClawHub API and save it to JSON.

Example:
  python fetch_clawhub_skills.py --output clawhub_skills.json --limit 100
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://clawhub.ai/api/v1/skills"


def fetch_page(limit: int, offset: int, sort: str, timeout: int) -> List[Dict[str, Any]]:
    query = urlencode({"limit": limit, "offset": offset, "sort": sort})
    url = f"{API_URL}?{query}"

    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "clawhub-skill-fetcher/0.1",
        },
        method="GET",
    )

    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        data = json.loads(body)

    # API may return either a list directly or a wrapped object.
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("skills", "items", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]

    raise ValueError("Unexpected API response shape; expected list or dict containing a list")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch one skills page from ClawHub and save to JSON.")
    parser.add_argument("--output", default="clawhub_skills.json", help="Output JSON file path.")
    parser.add_argument("--limit", type=int, default=100, help="Page size per request.")
    parser.add_argument("--sort", default="newest", help="Sort mode (e.g. newest).")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.limit <= 0:
        print("--limit must be > 0", file=sys.stderr)
        return 2

    try:
        skills = fetch_page(limit=args.limit, offset=0, sort=args.sort, timeout=args.timeout)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as err:
        print(f"Failed to fetch skills: {err}", file=sys.stderr)
        return 1

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(skills, f, indent=2)

    print(f"Saved {len(skills)} skills to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
