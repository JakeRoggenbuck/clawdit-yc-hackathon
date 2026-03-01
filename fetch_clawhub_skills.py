#!/usr/bin/env python3
"""Fetch skills, download ZIPs, and optionally audit SKILL.md with an LLM.

Examples:
  python fetch_clawhub_skills.py --output clawhub_skills.json --limit 100
  python fetch_clawhub_skills.py --download-slug gifgrep --skip-list-fetch
  python fetch_clawhub_skills.py --download-all-from-list --delay 1.0
  MINIMAX_API_KEY=... python fetch_clawhub_skills.py --download-all-from-list --audit-skill-md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import zipfile
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://clawhub.ai"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"
DEFAULT_MINIMAX_MODEL = "MiniMax-Text-01"
DEFAULT_LLM_PROVIDER = "minimax"

COLOR_RESET = "\033[0m"
COLOR_INFO = "\033[36m"
COLOR_WARN = "\033[33m"
COLOR_ERROR = "\033[31m"
COLOR_OK = "\033[32m"


def _supports_color() -> bool:
    return sys.stdout.isatty()


def log(level: str, message: str) -> None:
    color = ""
    if _supports_color():
        if level == "INFO":
            color = COLOR_INFO
        elif level == "WARN":
            color = COLOR_WARN
        elif level == "ERROR":
            color = COLOR_ERROR
        elif level == "OK":
            color = COLOR_OK

    prefix = f"[{level}]"
    ts = time.strftime("%H:%M:%S")
    if color:
        print(f"{color}{prefix}{COLOR_RESET} {ts} {message}")
        return
    print(f"{prefix} {ts} {message}")


def sleep_before_next(current_idx: int, total: int, delay: float, reason: str) -> None:
    if current_idx < total - 1 and delay > 0:
        log("INFO", f"Sleeping {delay:.2f}s before next skill ({reason})")
        time.sleep(delay)


def flush_audit_results(path: str, audit_results: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(audit_results, f, indent=2)
    log("INFO", f"Updated audit report: {path} ({len(audit_results)} entries)")


def load_audit_results(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as err:
        log("WARN", f"Could not load existing audit report {path}: {err}")
        return []
    if not isinstance(data, list):
        log("WARN", f"Ignoring {path}: expected top-level JSON array.")
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int) -> Dict[str, Any]:
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
            "User-Agent": "clawhub-skill-fetcher/0.3",
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


def extract_slug(skill: Dict[str, Any]) -> str | None:
    for key in ("slug", "name", "id"):
        value = skill.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_name(skill: Dict[str, Any]) -> str | None:
    for key in ("name", "title", "displayName"):
        value = skill.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_skill_md_text(zip_path: str, max_chars: int) -> Dict[str, Any]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        skill_md = None
        for name in names:
            if name.lower().endswith("skill.md"):
                skill_md = name
                break
        if skill_md is None:
            return {
                "found": False,
                "path_in_zip": None,
                "text": "",
                "truncated": False,
            }

        data = zf.read(skill_md)
        text = data.decode("utf-8", errors="replace")
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]

        return {
            "found": True,
            "path_in_zip": skill_md,
            "text": text,
            "truncated": truncated,
        }


def extract_openai_text(response: Dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]

    output = response.get("output")
    if isinstance(output, list):
        chunks: List[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("output_text", "text") and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
        if chunks:
            return "\n".join(chunks)

    return json.dumps(response, ensure_ascii=False)


def extract_minimax_text(response: Dict[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(choice.get("text"), str):
                return choice["text"]
    if isinstance(response.get("reply"), str):
        return response["reply"]
    return json.dumps(response, ensure_ascii=False)


def parse_json_dict_from_text(text: str) -> Dict[str, Any] | None:
    candidates: List[str] = []
    raw = text.strip()
    if raw:
        candidates.append(raw)

    # Common model shape: fenced markdown like ```json ... ```
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        unfenced = "\n".join(lines).strip()
        if unfenced:
            candidates.append(unfenced)

    # Try extracting the outermost JSON object from surrounding text.
    first_brace = raw.find("{")
    last_brace = raw.rfind("}")
    if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
        candidates.append(raw[first_brace : last_brace + 1].strip())

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def normalize_audit_shape(parsed: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    result = dict(parsed)
    if not isinstance(result.get("risk_level"), str):
        result["risk_level"] = "unknown"
    if not isinstance(result.get("dangerous"), bool):
        if result.get("dangerous") not in (None,):
            result["dangerous"] = None
    if not isinstance(result.get("summary"), str):
        result["summary"] = ""
    if not isinstance(result.get("findings"), list):
        result["findings"] = []
    result["_raw_text"] = raw_text
    return result


def audit_skill_md(
    provider: str,
    llm_base_url: str,
    api_key: str,
    model: str,
    slug: str,
    skill_md_text: str,
    timeout: int,
) -> Dict[str, Any]:
    prompt = (
        "You are a software security auditor reviewing OpenClaw SKILL.md instructions. "
        "Detect potentially dangerous content such as credential theft, data exfiltration, "
        "social engineering, hidden remote code execution, destructive commands, and obfuscation. "
        "Return strict JSON with keys: risk_level, dangerous, summary, findings. "
        "findings must be a list of objects with keys: severity, title, evidence, why, recommendation."
    )

    if provider == "minimax":
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Skill slug: {slug}\n\nSKILL.md:\n\n{skill_md_text}"},
            ],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        raw = post_json(llm_base_url, payload, headers, timeout)
        text = extract_minimax_text(raw).strip()
    else:
        payload = {
            "model": model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": prompt}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Skill slug: {slug}\n\nSKILL.md:\n\n{skill_md_text}",
                        }
                    ],
                },
            ],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        raw = post_json(llm_base_url, payload, headers, timeout)
        text = extract_openai_text(raw).strip()
    parsed = parse_json_dict_from_text(text)
    if parsed is not None:
        return normalize_audit_shape(parsed, text)

    return {
        "risk_level": "unknown",
        "dangerous": None,
        "summary": "Model response was not valid JSON.",
        "findings": [],
        "_raw_text": text,
    }


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
        "--download-name",
        action="append",
        default=[],
        help="Download by skill name from fetched list (repeat for multiple).",
    )
    parser.add_argument(
        "--download-dir",
        default="skill_zips",
        help="Directory to write downloaded ZIP files.",
    )
    parser.add_argument(
        "--download-all-from-list",
        action="store_true",
        help="Download all slugs found in the fetched list response.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between ZIP downloads when downloading multiple skills.",
    )
    parser.add_argument(
        "--skip-list-fetch",
        action="store_true",
        help="Skip /skills request and only run ZIP downloads for --download-slug values.",
    )
    parser.add_argument(
        "--audit-skill-md",
        action="store_true",
        help="After download, extract SKILL.md and run LLM safety audit.",
    )
    parser.add_argument(
        "--audit-output",
        default="skill_audit_report.json",
        help="Output JSON file for audit results.",
    )
    parser.add_argument(
        "--dont-cache",
        action="store_true",
        help="Ignore existing audit output and force re-auditing all processed skills.",
    )
    parser.add_argument(
        "--max-skill-md-chars",
        type=int,
        default=12000,
        help="Max SKILL.md characters sent to the LLM per skill.",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["minimax", "openai"],
        default=DEFAULT_LLM_PROVIDER,
        help="LLM provider for auditing.",
    )
    parser.add_argument(
        "--llm-api-key",
        default="",
        help="LLM API key (defaults to MINIMAX_API_KEY or OPENAI_API_KEY by provider).",
    )
    parser.add_argument(
        "--llm-model",
        default="",
        help="Model used for auditing (provider-specific default if omitted).",
    )
    parser.add_argument(
        "--llm-base-url",
        default="",
        help="Audit API URL (provider-specific default if omitted).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log("INFO", f"Starting run with base URL: {args.base_url}")

    if args.limit <= 0:
        log("ERROR", "--limit must be > 0")
        return 2

    skills: List[Dict[str, Any]] = []

    if not args.skip_list_fetch:
        log("INFO", f"Fetching skills list (limit={args.limit}, sort={args.sort})")
        try:
            skills = fetch_page(
                base_url=args.base_url,
                limit=args.limit,
                offset=0,
                sort=args.sort,
                timeout=args.timeout,
            )
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as err:
            log("ERROR", f"Failed to fetch skills: {err}")
            return 1

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(skills, f, indent=2)

        log("OK", f"Saved {len(skills)} skills to {args.output}")
        if args.delay > 0:
            log("INFO", f"Sleeping {args.delay:.2f}s after list fetch")
            time.sleep(args.delay)
    else:
        log("WARN", "Skipping skills list fetch (--skip-list-fetch)")

    all_slugs: List[str] = list(args.download_slug)
    if args.download_name:
        if args.skip_list_fetch:
            log("ERROR", "--download-name requires list fetch (remove --skip-list-fetch).")
            return 2
        for wanted_name in args.download_name:
            target = wanted_name.strip().lower()
            if not target:
                continue

            exact_matches: List[tuple[str, str]] = []
            fuzzy_matches: List[tuple[str, str]] = []
            for skill in skills:
                slug = extract_slug(skill)
                name = extract_name(skill)
                if not slug or not name:
                    continue
                current = name.lower()
                if current == target:
                    exact_matches.append((slug, name))
                elif target in current:
                    fuzzy_matches.append((slug, name))

            matches = exact_matches or fuzzy_matches
            if not matches:
                log("WARN", f"No skills matched name '{wanted_name}'")
                continue

            for slug, resolved_name in matches:
                all_slugs.append(slug)
                log("INFO", f"Matched name '{wanted_name}' -> slug '{slug}' ({resolved_name})")

    if args.download_all_from_list:
        if args.skip_list_fetch:
            log("ERROR", "--download-all-from-list requires list fetch (remove --skip-list-fetch).")
            return 2
        for skill in skills:
            slug = extract_slug(skill)
            if slug:
                all_slugs.append(slug)

    # Deduplicate while preserving order.
    deduped_slugs: List[str] = []
    seen = set()
    for slug in all_slugs:
        if slug not in seen:
            deduped_slugs.append(slug)
            seen.add(slug)

    audit_results: List[Dict[str, Any]] = []
    audited_slugs: set[str] = set()

    if not args.llm_api_key:
        env_var = "MINIMAX_API_KEY" if args.llm_provider == "minimax" else "OPENAI_API_KEY"
        args.llm_api_key = os.environ.get(env_var, "")
    if not args.llm_model:
        args.llm_model = DEFAULT_MINIMAX_MODEL if args.llm_provider == "minimax" else DEFAULT_OPENAI_MODEL
    if not args.llm_base_url:
        args.llm_base_url = DEFAULT_MINIMAX_BASE_URL if args.llm_provider == "minimax" else DEFAULT_OPENAI_BASE_URL

    if args.audit_skill_md and not args.llm_api_key:
        env_var = "MINIMAX_API_KEY" if args.llm_provider == "minimax" else "OPENAI_API_KEY"
        log("ERROR", f"Missing API key. Set --llm-api-key or {env_var}.")
        return 2

    if args.audit_skill_md and not args.dont_cache:
        audit_results = load_audit_results(args.audit_output)
        audited_slugs = {
            entry["slug"]
            for entry in audit_results
            if isinstance(entry.get("slug"), str) and isinstance(entry.get("audit"), dict)
        }
        if audited_slugs:
            log(
                "INFO",
                f"Loaded {len(audited_slugs)} existing audited slug(s) from {args.audit_output}",
            )
    elif args.audit_skill_md and args.dont_cache:
        log("INFO", "--dont-cache set; ignoring existing audit cache and forcing re-audit")

    processed_count = 0
    if deduped_slugs:
        log("INFO", f"Processing {len(deduped_slugs)} skill(s)")
        for idx, slug in enumerate(deduped_slugs):
            log("INFO", f"[{idx + 1}/{len(deduped_slugs)}] Downloading {slug}")
            try:
                out_path = download_zip(
                    base_url=args.base_url,
                    slug=slug,
                    output_dir=args.download_dir,
                    timeout=args.timeout,
                )
                log("OK", f"Downloaded {slug} -> {out_path}")
                processed_count += 1
            except (HTTPError, URLError, TimeoutError) as err:
                log("ERROR", f"Failed to download slug '{slug}': {err}")
                if args.audit_skill_md:
                    audit_results.append(
                        {
                            "slug": slug,
                            "downloaded": False,
                            "error": f"Download failed: {err}",
                        }
                    )
                    flush_audit_results(args.audit_output, audit_results)
                sleep_before_next(idx, len(deduped_slugs), args.delay, "download failure")
                continue

            # Sequential mode: audit each skill immediately after download.
            if args.audit_skill_md:
                if slug in audited_slugs:
                    log(
                        "INFO",
                        f"{slug}: audit already found in {args.audit_output}; skipping re-audit",
                    )
                    sleep_before_next(idx, len(deduped_slugs), args.delay, "already audited")
                    continue

                log("INFO", f"Extracting SKILL.md for {slug}")
                try:
                    md_info = extract_skill_md_text(out_path, max_chars=args.max_skill_md_chars)
                except (OSError, zipfile.BadZipFile) as err:
                    audit_results.append(
                        {
                            "slug": slug,
                            "zip_path": out_path,
                            "error": f"Failed to read ZIP: {err}",
                        }
                    )
                    flush_audit_results(args.audit_output, audit_results)
                    log("ERROR", f"{slug}: failed to read ZIP: {err}")
                    sleep_before_next(idx, len(deduped_slugs), args.delay, "zip read failure")
                    continue

                if not md_info["found"]:
                    audit_results.append(
                        {
                            "slug": slug,
                            "zip_path": out_path,
                            "skill_md_found": False,
                            "summary": "SKILL.md not found in ZIP.",
                        }
                    )
                    flush_audit_results(args.audit_output, audit_results)
                    log("WARN", f"{slug}: SKILL.md not found in ZIP")
                    sleep_before_next(idx, len(deduped_slugs), args.delay, "missing SKILL.md")
                    continue

                log(
                    "INFO",
                    f"Auditing {slug} with provider={args.llm_provider} model={args.llm_model}"
                    + (" (truncated SKILL.md)" if md_info["truncated"] else ""),
                )
                try:
                    audit = audit_skill_md(
                        provider=args.llm_provider,
                        llm_base_url=args.llm_base_url,
                        api_key=args.llm_api_key,
                        model=args.llm_model,
                        slug=slug,
                        skill_md_text=md_info["text"],
                        timeout=args.timeout,
                    )
                except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as err:
                    audit_results.append(
                        {
                            "slug": slug,
                            "zip_path": out_path,
                            "skill_md_found": True,
                            "skill_md_path": md_info["path_in_zip"],
                            "skill_md_truncated": md_info["truncated"],
                            "error": f"LLM audit failed: {err}",
                        }
                    )
                    flush_audit_results(args.audit_output, audit_results)
                    log("ERROR", f"{slug}: LLM audit failed: {err}")
                    sleep_before_next(idx, len(deduped_slugs), args.delay, "audit API failure")
                    continue

                audit_results.append(
                    {
                        "slug": slug,
                        "zip_path": out_path,
                        "skill_md_found": True,
                        "skill_md_path": md_info["path_in_zip"],
                        "skill_md_truncated": md_info["truncated"],
                        "audit": audit,
                    }
                )
                audited_slugs.add(slug)
                flush_audit_results(args.audit_output, audit_results)
                log("OK", f"Audited {slug}")

            sleep_before_next(idx, len(deduped_slugs), args.delay, "completed")
    else:
        log("WARN", "No slugs to process")

    if args.audit_skill_md:
        if processed_count == 0:
            log("ERROR", "No downloaded ZIPs to audit.")
            return 2

        flush_audit_results(args.audit_output, audit_results)
        log("OK", f"Saved final audit results to {args.audit_output}")

    log("OK", "Run completed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
