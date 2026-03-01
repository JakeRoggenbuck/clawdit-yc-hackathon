#!/usr/bin/env python3
"""Shared email alert helpers for puller/audit scripts."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Set

import kronicler
from agentmail import AgentMail
from dotenv import load_dotenv

load_dotenv()

KNOWN_LEVELS = {"critical", "high", "medium", "low", "info", "unknown"}


def parse_alert_levels(raw: str) -> Set[str]:
    levels: Set[str] = set()
    for token in raw.split(","):
        level = token.strip().lower()
        if level in KNOWN_LEVELS:
            levels.add(level)
    if not levels:
        return {"critical", "high"}
    return levels


def add_alert_mail_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--alert-email-to",
        default=os.environ.get("ALERT_EMAIL_TO", os.environ.get("ADMIN_EMAIL", "")),
        help="Destination email for alert notifications.",
    )
    parser.add_argument(
        "--agentmail-api-key",
        default=os.environ.get("AGENTMAIL_API_KEY", ""),
        help="AgentMail API key.",
    )
    parser.add_argument(
        "--agentmail-inbox-id",
        default=os.environ.get("AGENTMAIL_INBOX_ID", "gracefulbird586@agentmail.to"),
        help="AgentMail inbox ID used to send alerts.",
    )
    parser.add_argument(
        "--alert-levels",
        default=os.environ.get("ALERT_LEVELS", "critical,high"),
        help="Comma-separated severities that trigger email alerts.",
    )
    parser.add_argument(
        "--alert-email-subject-prefix",
        default=os.environ.get("ALERT_EMAIL_SUBJECT_PREFIX", "[Puller Alert]"),
        help="Prefix prepended to alert email subjects.",
    )


@dataclass
class AlertMailer:
    client: AgentMail
    inbox_id: str
    to_email: str
    subject_prefix: str
    levels: Set[str]

    @kronicler.capture
    def send(self, subject: str, body: str) -> None:
        self.client.inboxes.messages.send(
            inbox_id=self.inbox_id,
            to=[self.to_email],
            subject=subject,
            text=body,
        )


def build_alert_mailer(args: argparse.Namespace, log: Callable[[str, str], None]) -> AlertMailer | None:
    to_email = (args.alert_email_to or "").strip()
    if not to_email:
        return None

    agentmail_api_key = (args.agentmail_api_key or "").strip()
    inbox_id = (args.agentmail_inbox_id or "").strip()
    if not agentmail_api_key or not inbox_id:
        log(
            "WARN",
            "Alert email disabled: --alert-email-to requires --agentmail-api-key and --agentmail-inbox-id.",
        )
        return None

    levels = parse_alert_levels(args.alert_levels or "")
    return AlertMailer(
        client=AgentMail(api_key=agentmail_api_key),
        inbox_id=inbox_id,
        to_email=to_email,
        subject_prefix=(args.alert_email_subject_prefix or "").strip() or "[Puller Alert]",
        levels=levels,
    )


def _triggering_findings(audit: Dict[str, Any], levels: Set[str]) -> List[Dict[str, Any]]:
    findings = audit.get("findings")
    if not isinstance(findings, list):
        return []
    out: List[Dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity", "unknown")).strip().lower()
        if severity in levels:
            out.append(finding)
    return out


def maybe_send_alert_email(
    mailer: AlertMailer | None,
    source_name: str,
    slug: str,
    audit: Dict[str, Any],
    zip_path: str,
    log: Callable[[str, str], None],
) -> None:
    if mailer is None:
        return

    risk_level = str(audit.get("risk_level", "unknown")).strip().lower()
    triggering = _triggering_findings(audit, mailer.levels)
    if risk_level not in mailer.levels and not triggering:
        return

    summary = str(audit.get("summary", "")).strip()
    subject = f"{mailer.subject_prefix} {source_name} {slug} ({risk_level})"

    body_lines = [
        "A puller alert matched your configured severity threshold.",
        "",
        f"Timestamp (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"Source: {source_name}",
        f"Slug: {slug}",
        f"Risk level: {risk_level}",
        f"Zip path: {zip_path}",
        f"Alert levels: {', '.join(sorted(mailer.levels))}",
        "",
        "Summary:",
        summary or "(empty)",
        "",
        f"Matching findings: {len(triggering)}",
    ]

    for idx, finding in enumerate(triggering[:10], start=1):
        severity = str(finding.get("severity", "unknown")).strip().lower()
        title = str(finding.get("title", "")).strip() or "(untitled)"
        body_lines.append(f"{idx}. [{severity}] {title}")

    if len(triggering) > 10:
        body_lines.append(f"... and {len(triggering) - 10} more")

    try:
        mailer.send(subject=subject, body="\n".join(body_lines))
        log("OK", f"{slug}: sent alert email to {mailer.to_email}")
    except Exception as err:  # pragma: no cover - network/auth errors are runtime dependent.
        log("WARN", f"{slug}: failed to send alert email: {err}")
