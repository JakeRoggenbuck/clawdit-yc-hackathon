#!/usr/bin/env python3
"""Fetch skills from SkillsMP, build ZIPs with SKILL.md, and optionally audit with an LLM.

Examples:
  python fetch_skillsmp_skills.py --output skillsmp_skills.json --category backend --sort-by recent
  python fetch_skillsmp_skills.py --download-all-from-list --category backend --sort-by recent
  OPENAI_API_KEY=... python fetch_skillsmp_skills.py --download-all-from-list --audit-skill-md
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import zipfile
from typing import Any, Dict, Iterable, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://skillsmp.com"
DEFAULT_API_PATH = "/api/skills"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1/responses"
DEFAULT_AUDIT_MODEL = "gpt-4.1-mini"

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


def join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def request_json_with_retries(
    url: str,
    timeout: int,
    retries: int,
    retry_delay: float,
    headers: Dict[str, str] | None = None,
) -> Any:
    headers = headers or {}
    attempt = 0
    while True:
        attempt += 1
        req = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "skillsmp-fetcher/0.1",
                **headers,
            },
            method="GET",
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
        except HTTPError as err:
            retryable = err.code >= 500 or err.code == 429
            if not retryable or attempt > retries:
                raise
            backoff = retry_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            log("WARN", f"{url} returned HTTP {err.code}; retrying in {backoff:.2f}s ({attempt}/{retries})")
            time.sleep(backoff)
        except (URLError, TimeoutError, json.JSONDecodeError) as err:
            if attempt > retries:
                raise
            backoff = retry_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            log("WARN", f"{url} failed ({err}); retrying in {backoff:.2f}s ({attempt}/{retries})")
            time.sleep(backoff)


def safe_slug_filename(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", slug)


def extract_skill_items(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        for key in ("skills", "items", "results", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = extract_skill_items(value)
                if nested:
                    return nested

    return []


def fetch_page(
    base_url: str,
    api_path: str,
    page: int,
    limit: int,
    sort_by: str,
    category: str,
    search: str,
    timeout: int,
    retries: int,
    retry_delay: float,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {
        "page": page,
        "limit": limit,
        "sortBy": sort_by,
    }
    if category:
        query["category"] = category
    if search:
        query["search"] = search

    url = f"{join_url(base_url, api_path)}?{urlencode(query)}"
    payload = request_json_with_retries(
        url=url,
        timeout=timeout,
        retries=retries,
        retry_delay=retry_delay,
    )

    items = extract_skill_items(payload)
    if items:
        return items

    raise ValueError("Unexpected API response shape; expected list or dict containing a list")


def extract_slug(skill: Dict[str, Any]) -> str | None:
    for key in ("slug", "fullSlug", "name", "id"):
        value = skill.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    owner = skill.get("owner")
    repo = skill.get("repo")
    if isinstance(owner, str) and owner and isinstance(repo, str) and repo:
        skill_name = skill.get("skill") or skill.get("skillName") or skill.get("name")
        if isinstance(skill_name, str) and skill_name:
            return f"{owner}/{repo}/{skill_name}"
        return f"{owner}/{repo}"

    return None


def iter_urls(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        if obj.startswith("http://") or obj.startswith("https://"):
            yield obj
        return
    if isinstance(obj, dict):
        for value in obj.values():
            yield from iter_urls(value)
        return
    if isinstance(obj, list):
        for value in obj:
            yield from iter_urls(value)


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


def skill_md_from_skill_object(skill: Dict[str, Any]) -> str | None:
    for key in ("content", "skillMd", "skill_md", "markdown", "instructions", "readme"):
        value = skill.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def build_skill_md_candidates(skill: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if url not in seen:
            seen.add(url)
            candidates.append(url)

    for url in iter_urls(skill):
        if "github.com" in url:
            raw = github_blob_to_raw(url)
            if raw:
                add(raw)
            elif urlparse(url).netloc == "github.com":
                parts = [part for part in urlparse(url).path.split("/") if part]
                if len(parts) >= 2:
                    owner, repo = parts[0], parts[1]
                    for branch in ("main", "master"):
                        add(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/SKILL.md")

    owner = skill.get("owner")
    repo = skill.get("repo")
    skill_name = skill.get("skill") or skill.get("skillName") or skill.get("name")
    if isinstance(owner, str) and isinstance(repo, str):
        for branch in ("main", "master"):
            if isinstance(skill_name, str) and skill_name:
                add(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{skill_name}/SKILL.md")
                add(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/skills/{skill_name}/SKILL.md")
            add(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/SKILL.md")

    return candidates


def try_fetch_skill_detail(
    base_url: str,
    timeout: int,
    retries: int,
    retry_delay: float,
    slug: str,
    skill: Dict[str, Any],
) -> Dict[str, Any] | None:
    candidates: List[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        full = join_url(base_url, path)
        if full not in seen:
            seen.add(full)
            candidates.append(full)

    skill_id = skill.get("id")
    if isinstance(skill_id, str) and skill_id:
        add(f"api/skills/{skill_id}")
        add(f"api/v1/skills/{skill_id}")

    if slug:
        add(f"api/skills/{slug}")
        add(f"api/v1/skills/{slug}")

    owner = skill.get("owner")
    repo = skill.get("repo")
    if isinstance(owner, str) and owner and isinstance(repo, str) and repo:
        add(f"api/v1/skills/{owner}/{repo}")

    for url in candidates:
        try:
            payload = request_json_with_retries(
                url=url,
                timeout=timeout,
                retries=retries,
                retry_delay=retry_delay,
            )
        except Exception:
            continue
        items = extract_skill_items(payload)
        if items:
            return items[0]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                return data
    return None


def fetch_url_text_with_retries(url: str, timeout: int, retries: int, retry_delay: float) -> str:
    attempt = 0
    while True:
        attempt += 1
        req = Request(
            url,
            headers={
                "Accept": "text/plain,*/*",
                "User-Agent": "skillsmp-fetcher/0.1",
            },
            method="GET",
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except HTTPError as err:
            retryable = err.code >= 500 or err.code == 429
            if not retryable or attempt > retries:
                raise
            backoff = retry_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            log("WARN", f"{url} returned HTTP {err.code}; retrying in {backoff:.2f}s ({attempt}/{retries})")
            time.sleep(backoff)
        except (URLError, TimeoutError) as err:
            if attempt > retries:
                raise
            backoff = retry_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            log("WARN", f"{url} failed ({err}); retrying in {backoff:.2f}s ({attempt}/{retries})")
            time.sleep(backoff)


def resolve_skill_md_text(
    base_url: str,
    slug: str,
    skill: Dict[str, Any],
    timeout: int,
    retries: int,
    retry_delay: float,
) -> Dict[str, Any]:
    direct = skill_md_from_skill_object(skill)
    if direct:
        return {
            "text": direct,
            "source_url": "embedded API content",
            "source_type": "api content",
        }

    detail = try_fetch_skill_detail(
        base_url=base_url,
        timeout=timeout,
        retries=retries,
        retry_delay=retry_delay,
        slug=slug,
        skill=skill,
    )
    if detail:
        direct = skill_md_from_skill_object(detail)
        if direct:
            return {
                "text": direct,
                "source_url": "detail API content",
                "source_type": "api detail",
            }
        skill = {**skill, **detail}

    for candidate in build_skill_md_candidates(skill):
        try:
            text = fetch_url_text_with_retries(candidate, timeout=timeout, retries=retries, retry_delay=retry_delay)
        except Exception:
            continue
        if text.strip():
            return {
                "text": text,
                "source_url": candidate,
                "source_type": "raw.githubusercontent.com",
            }

    raise ValueError("Could not resolve SKILL.md content from API fields or repo links")


def download_zip(
    base_url: str,
    slug: str,
    skill: Dict[str, Any],
    output_dir: str,
    timeout: int,
    retries: int,
    retry_delay: float,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    out_name = f"{safe_slug_filename(slug)}.zip"
    out_path = os.path.join(output_dir, out_name)

    skill_doc = resolve_skill_md_text(
        base_url=base_url,
        slug=slug,
        skill=skill,
        timeout=timeout,
        retries=retries,
        retry_delay=retry_delay,
    )

    zip_root = safe_slug_filename(slug)
    metadata = {
        "slug": slug,
        "source_url": skill_doc["source_url"],
        "source_type": skill_doc["source_type"],
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{zip_root}/SKILL.md", skill_doc["text"])
        zf.writestr(f"{zip_root}/metadata.json", json.dumps(metadata, indent=2))

    return out_path


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
    parser = argparse.ArgumentParser(description="Fetch SkillsMP list and optionally download/audit SKILL.md.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL (default: https://skillsmp.com).")
    parser.add_argument("--api-path", default=DEFAULT_API_PATH, help="API list path (default: /api/skills).")
    parser.add_argument("--output", default="skillsmp_skills.json", help="Output JSON file path.")
    parser.add_argument("--page", type=int, default=1, help="Page number for list API.")
    parser.add_argument("--limit", type=int, default=12, help="Page size for list API.")
    parser.add_argument("--sort-by", default="recent", help="Sort field (e.g. recent, stars).")
    parser.add_argument("--category", default="", help="Category filter (e.g. backend).")
    parser.add_argument("--search", default="", help="Search query (optional).")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    parser.add_argument("--retries", type=int, default=5, help="Retry attempts for transient API/network errors.")
    parser.add_argument("--retry-delay", type=float, default=1.0, help="Base seconds for exponential backoff.")
    parser.add_argument(
        "--download-slug",
        action="append",
        default=[],
        help="Download ZIP for a skill slug (repeat for multiple).",
    )
    parser.add_argument("--download-dir", default="skill_zips", help="Directory to write downloaded ZIP files.")
    parser.add_argument(
        "--download-all-from-list",
        action="store_true",
        help="Download all slugs found in the list response.",
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
        help="Skip list API call and only run ZIP downloads for --download-slug values.",
    )
    parser.add_argument("--audit-skill-md", action="store_true", help="Audit SKILL.md content with an LLM.")
    parser.add_argument("--audit-output", default="skill_audit_report.json", help="Output JSON file for audit results.")
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
    parser.add_argument("--openai-model", default=DEFAULT_AUDIT_MODEL, help="Model used for auditing.")
    parser.add_argument(
        "--openai-base-url",
        default=DEFAULT_OPENAI_BASE_URL,
        help="OpenAI Responses API URL.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log("INFO", f"Starting run with base URL: {args.base_url}")

    if args.limit <= 0:
        log("ERROR", "--limit must be > 0")
        return 2
    if args.retries < 0:
        log("ERROR", "--retries must be >= 0")
        return 2

    skills: List[Dict[str, Any]] = []

    if not args.skip_list_fetch:
        log(
            "INFO",
            f"Fetching SkillsMP list page={args.page} limit={args.limit} sortBy={args.sort_by}"
            + (f" category={args.category}" if args.category else "")
            + (f" search={args.search}" if args.search else ""),
        )
        try:
            skills = fetch_page(
                base_url=args.base_url,
                api_path=args.api_path,
                page=args.page,
                limit=args.limit,
                sort_by=args.sort_by,
                category=args.category,
                search=args.search,
                timeout=args.timeout,
                retries=args.retries,
                retry_delay=args.retry_delay,
            )
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as err:
            log("ERROR", f"Failed to fetch skills: {err}")
            return 1

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(skills, f, indent=2)

        log("OK", f"Saved {len(skills)} skills to {args.output}")
    else:
        log("WARN", "Skipping skills list fetch (--skip-list-fetch)")

    skill_by_slug: Dict[str, Dict[str, Any]] = {}
    for skill in skills:
        slug = extract_slug(skill)
        if slug:
            skill_by_slug[slug] = skill

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
            skill = skill_by_slug.get(slug, {"slug": slug})
            try:
                out_path = download_zip(
                    base_url=args.base_url,
                    slug=slug,
                    skill=skill,
                    output_dir=args.download_dir,
                    timeout=args.timeout,
                    retries=args.retries,
                    retry_delay=args.retry_delay,
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
