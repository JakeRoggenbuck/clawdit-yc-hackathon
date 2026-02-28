#!/usr/bin/env python3
"""Fetch one skills list page and optionally download skill ZIP files.

Example:
  python fetch_clawhub_skills.py --output clawhub_skills.json --limit 100
  python fetch_clawhub_skills.py --download-slug gifgrep
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://clawhub.ai"


def join_api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def fetch_page(base_url: str, limit: int, offset: int, sort: str, timeout: int) -> List[Dict[str, Any]]:
    api_url = join_api_url(base_url, "/api/v1/skills")
    query = urlencode({"limit": limit, "offset": offset, "sort": sort})
    url = f"{api_url}?{query}"

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


def safe_slug_filename(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", slug)


def download_zip(base_url: str, slug: str, output_dir: str, timeout: int) -> str:
    os.makedirs(output_dir, exist_ok=True)
    url = join_api_url(base_url, f"/api/v1/download?{urlencode({'slug': slug})}")
    out_name = f"{safe_slug_filename(slug)}.zip"
    out_path = os.path.join(output_dir, out_name)

    req = Request(
        url,
        headers={
            "Accept": "application/zip, application/octet-stream, */*",
            "User-Agent": "clawhub-skill-fetcher/0.2",
        },
        method="GET",
    )

    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()

    with open(out_path, "wb") as f:
        f.write(data)

    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch one skills page and optionally download skill ZIP files.")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="API host base URL (e.g. https://clawhub.ai or your Convex site URL).",
    )
    parser.add_argument("--output", default="clawhub_skills.json", help="Output JSON file path.")
    parser.add_argument("--limit", type=int, default=100, help="Page size per request.")
    parser.add_argument("--sort", default="newest", help="Sort mode (e.g. newest).")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--download-slug",
        action="append",
        default=[],
        help="Download ZIP for a skill slug (repeat for multiple).",
    )
    parser.add_argument(
        "--download-dir",
        default="skill_zips",
        help="Directory to write downloaded ZIP files.",
    )
    parser.add_argument(
        "--skip-list-fetch",
        action="store_true",
        help="Skip /skills request and only run ZIP downloads for --download-slug values.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.limit <= 0:
        print("--limit must be > 0", file=sys.stderr)
        return 2

    if not args.skip_list_fetch:
        try:
            skills = fetch_page(
                base_url=args.base_url,
                limit=args.limit,
                offset=0,
                sort=args.sort,
                timeout=args.timeout,
            )
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as err:
            print(f"Failed to fetch skills: {err}", file=sys.stderr)
            return 1

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(skills, f, indent=2)

        print(f"Saved {len(skills)} skills to {args.output}")

    if args.download_slug:
        for slug in args.download_slug:
            try:
                out_path = download_zip(
                    base_url=args.base_url,
                    slug=slug,
                    output_dir=args.download_dir,
                    timeout=args.timeout,
                )
                print(f"Downloaded {slug} -> {out_path}")
            except (HTTPError, URLError, TimeoutError) as err:
                print(f"Failed to download slug '{slug}': {err}", file=sys.stderr)
                return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
