#!/usr/bin/env python3
"""Fetch skills from skills.sh, build ZIPs with SKILL.md, and optionally audit with an LLM.

Examples:
  python fetch_skills_sh_skills.py --output skills_sh_skills.json --limit 100
  python fetch_skills_sh_skills.py --download-slug vercel-labs/skills/find-skills --skip-list-fetch
  python fetch_skills_sh_skills.py --download-all-from-list --delay 1.0
  OPENAI_API_KEY=... python fetch_skills_sh_skills.py --download-all-from-list --audit-skill-md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import zipfile
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from alert_mail import add_alert_mail_args, build_alert_mailer, maybe_send_alert_email

DEFAULT_BASE_URL = "https://skills.sh"
DEFAULT_LIST_URL = "https://skills.sh/hot"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1/responses"
DEFAULT_AUDIT_MODEL = "gpt-4.1-mini"

COLOR_RESET = "\033[0m"
COLOR_INFO = "\033[36m"
COLOR_WARN = "\033[33m"
COLOR_ERROR = "\033[31m"
COLOR_OK = "\033[32m"


class SkillsListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._seen: set[str] = set()
        self.skill_paths: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = None
        for key, value in attrs:
            if key == "href":
                href = value
                break
        if not href:
            return

        parsed = urlparse(href)
        path = parsed.path or ""
        if parsed.scheme and parsed.netloc and parsed.netloc != "skills.sh":
            return

        parts = [part for part in path.split("/") if part]
        # skills.sh skill page path shape is usually /owner/repo/skill
        if len(parts) != 3:
            return
        candidate = "/" + "/".join(parts)
        if candidate not in self._seen:
            self._seen.add(candidate)
            self.skill_paths.append(candidate)


class SkillPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: List[str] = []
        self._pre_depth = 0
        self._code_depth = 0
        self._current_code: List[str] = []
        self.code_blocks: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        if tag == "a":
            for key, value in attrs:
                if key == "href" and value:
                    self.hrefs.append(value)
                    break
        if tag == "pre":
            self._pre_depth += 1
        if tag == "code":
            self._code_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "code":
            self._code_depth = max(0, self._code_depth - 1)
            if self._pre_depth > 0 and self._code_depth == 0 and self._current_code:
                text = "".join(self._current_code).strip()
                if text:
                    self.code_blocks.append(unescape(text))
                self._current_code = []
        if tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._pre_depth > 0 and self._code_depth > 0:
            self._current_code.append(data)


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


def fetch_url_text(url: str, timeout: int) -> str:
    req = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "skills-sh-fetcher/0.1",
        },
        method="GET",
    )
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_list_page(list_url: str, limit: int, timeout: int) -> List[Dict[str, Any]]:
    html = fetch_url_text(list_url, timeout=timeout)
    parser = SkillsListParser()
    parser.feed(html)

    skills: List[Dict[str, Any]] = []
    for path in parser.skill_paths[:limit]:
        slug = path.lstrip("/")
        skills.append(
            {
                "slug": slug,
                "url": urljoin(DEFAULT_BASE_URL, path),
                "source": "skills.sh leaderboard scrape",
            }
        )

    if not skills:
        raise ValueError("No skills discovered on list page.")
    return skills


def safe_slug_filename(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", slug)


def github_blob_to_raw(github_url: str) -> str | None:
    parsed = urlparse(github_url)
    if parsed.netloc != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 5:
        return None
    owner, repo, mode = parts[0], parts[1], parts[2]
    if mode == "blob":
        branch = parts[3]
        path = "/".join(parts[4:])
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    if mode == "tree":
        branch = parts[3]
        path = "/".join(parts[4:])
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}/SKILL.md"
    return None


def build_raw_skill_md_candidates(skill_url: str, page_hrefs: List[str]) -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()

    def add_candidate(url: str) -> None:
        if url not in seen:
            seen.add(url)
            candidates.append(url)

    for href in page_hrefs:
        absolute = urljoin(skill_url, href)
        raw_url = github_blob_to_raw(absolute)
        if raw_url and raw_url.lower().endswith("skill.md"):
            add_candidate(raw_url)

    path_parts = [part for part in urlparse(skill_url).path.split("/") if part]
    if len(path_parts) >= 3:
        owner, repo, skill_name = path_parts[0], path_parts[1], path_parts[2]
        for branch in ("main", "master"):
            add_candidate(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{skill_name}/SKILL.md")
            add_candidate(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/skills/{skill_name}/SKILL.md")
            add_candidate(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/SKILL.md")

    return candidates


def fetch_skill_md_from_skill_page(skill_url: str, timeout: int) -> Dict[str, Any]:
    page_html = fetch_url_text(skill_url, timeout=timeout)
    parser = SkillPageParser()
    parser.feed(page_html)

    for raw_url in build_raw_skill_md_candidates(skill_url, parser.hrefs):
        try:
            text = fetch_url_text(raw_url, timeout=timeout)
        except (HTTPError, URLError, TimeoutError):
            continue
        if text.strip():
            return {
                "text": text,
                "source_url": raw_url,
                "source_type": "raw.githubusercontent.com",
            }

    # Fallback: use longest code block on the skill page if no raw source was discoverable.
    if parser.code_blocks:
        best = max(parser.code_blocks, key=len)
        if best.strip():
            return {
                "text": best,
                "source_url": skill_url,
                "source_type": "skills.sh code block fallback",
            }

    raise ValueError("Unable to locate SKILL.md content on skill page")


def download_zip(base_url: str, slug: str, output_dir: str, timeout: int) -> str:
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"{safe_slug_filename(slug)}.zip"
    out_path = os.path.join(output_dir, out_name)

    skill_url = urljoin(base_url.rstrip("/") + "/", slug.lstrip("/"))
    skill_doc = fetch_skill_md_from_skill_page(skill_url, timeout=timeout)

    zip_root = safe_slug_filename(slug)
    metadata = {
        "slug": slug,
        "skill_url": skill_url,
        "source_url": skill_doc["source_url"],
        "source_type": skill_doc["source_type"],
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{zip_root}/SKILL.md", skill_doc["text"])
        zf.writestr(f"{zip_root}/metadata.json", json.dumps(metadata, indent=2))

    return out_path


def extract_slug(skill: Dict[str, Any]) -> str | None:
    for key in ("slug", "name", "id"):
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


def parse_json_dict_from_text(text: str) -> Dict[str, Any] | None:
    candidates: List[str] = []
    raw = text.strip()
    if raw:
        candidates.append(raw)

    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        unfenced = "\n".join(lines).strip()
        if unfenced:
            candidates.append(unfenced)

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
    openai_base_url: str,
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

    raw = post_json(openai_base_url, payload, headers, timeout)
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
    parser = argparse.ArgumentParser(description="Fetch skills.sh skill list and optionally download/audit SKILL.md.")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL for skill page paths (default: https://skills.sh).",
    )
    parser.add_argument(
        "--list-url",
        default=DEFAULT_LIST_URL,
        help="skills.sh list page URL used for scraping (default: hot feed).",
    )
    parser.add_argument("--output", default="skills_sh_skills.json", help="Output JSON file path.")
    parser.add_argument("--limit", type=int, default=100, help="Max skills scraped from list page.")
    parser.add_argument("--sort", default="leaderboard", help="Compatibility arg; currently informational only.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--download-slug",
        action="append",
        default=[],
        help="Download ZIP for a skill slug (e.g. owner/repo/skill). Repeat for multiple.",
    )
    parser.add_argument(
        "--download-dir",
        default="skill_zips",
        help="Directory to write downloaded ZIP files.",
    )
    parser.add_argument(
        "--download-all-from-list",
        action="store_true",
        help="Download all slugs found in the scraped list response.",
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
        help="Skip list scraping and only run ZIP downloads for --download-slug values.",
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
        "--openai-api-key",
        default=os.environ.get("OPENAI_API_KEY", ""),
        help="OpenAI API key (defaults to OPENAI_API_KEY env var).",
    )
    parser.add_argument(
        "--openai-model",
        default=DEFAULT_AUDIT_MODEL,
        help="Model used for auditing.",
    )
    parser.add_argument(
        "--openai-base-url",
        default=DEFAULT_OPENAI_BASE_URL,
        help="OpenAI Responses API URL.",
    )
    add_alert_mail_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log("INFO", f"Starting run with base URL: {args.base_url}")
    alert_mailer = build_alert_mailer(args, log)

    if args.limit <= 0:
        log("ERROR", "--limit must be > 0")
        return 2

    skills: List[Dict[str, Any]] = []

    if not args.skip_list_fetch:
        log("INFO", f"Scraping skills list (limit={args.limit}, sort={args.sort})")
        try:
            skills = fetch_list_page(
                list_url=args.list_url,
                limit=args.limit,
                timeout=args.timeout,
            )
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as err:
            log("ERROR", f"Failed to scrape skills list: {err}")
            return 1

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(skills, f, indent=2)

        log("OK", f"Saved {len(skills)} skills to {args.output}")
    else:
        log("WARN", "Skipping skills list fetch (--skip-list-fetch)")

    all_slugs: List[str] = list(args.download_slug)
    if args.download_all_from_list:
        if args.skip_list_fetch:
            log("ERROR", "--download-all-from-list requires list fetch (remove --skip-list-fetch).")
            return 2
        for skill in skills:
            slug = extract_slug(skill)
            if slug:
                all_slugs.append(slug)

    deduped_slugs: List[str] = []
    seen = set()
    for slug in all_slugs:
        if slug not in seen:
            deduped_slugs.append(slug)
            seen.add(slug)

    audit_results: List[Dict[str, Any]] = []
    audited_slugs: set[str] = set()

    if args.audit_skill_md and not args.openai_api_key:
        log("ERROR", "Missing OpenAI API key. Set --openai-api-key or OPENAI_API_KEY.")
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
            except (HTTPError, URLError, TimeoutError, ValueError) as err:
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
                    f"Auditing {slug} with model={args.openai_model}"
                    + (" (truncated SKILL.md)" if md_info["truncated"] else ""),
                )
                try:
                    audit = audit_skill_md(
                        openai_base_url=args.openai_base_url,
                        api_key=args.openai_api_key,
                        model=args.openai_model,
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
                maybe_send_alert_email(
                    mailer=alert_mailer,
                    source_name="skills.sh",
                    slug=slug,
                    audit=audit,
                    zip_path=out_path,
                    log=log,
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
