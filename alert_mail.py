#!/usr/bin/env python3
"""Shared email alert helpers for puller/audit scripts."""

from __future__ import annotations

import argparse
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Set

KNOWN_LEVELS = {"critical", "high", "medium", "low", "info", "unknown"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
        default=os.environ.get("ALERT_EMAIL_TO", ""),
        help="Destination email for alert notifications.",
    )
    parser.add_argument(
        "--alert-email-from",
        default=os.environ.get("ALERT_EMAIL_FROM", ""),
        help="Sender email address for alert notifications.",
    )
    parser.add_argument(
        "--alert-email-smtp-host",
        default=os.environ.get("ALERT_EMAIL_SMTP_HOST", ""),
        help="SMTP host used to send alert emails.",
    )
    parser.add_argument(
        "--alert-email-smtp-port",
        type=int,
        default=int(os.environ.get("ALERT_EMAIL_SMTP_PORT", "587")),
        help="SMTP port used to send alert emails.",
    )
    parser.add_argument(
        "--alert-email-smtp-user",
        default=os.environ.get("ALERT_EMAIL_SMTP_USER", ""),
        help="SMTP username (optional).",
    )
    parser.add_argument(
        "--alert-email-smtp-password",
        default=os.environ.get("ALERT_EMAIL_SMTP_PASSWORD", ""),
        help="SMTP password (optional).",
    )
    parser.add_argument(
        "--alert-email-use-ssl",
        action="store_true",
        default=_env_bool("ALERT_EMAIL_USE_SSL", False),
        help="Use SMTP over SSL (smtplib.SMTP_SSL).",
    )
    parser.add_argument(
        "--alert-email-use-starttls",
        action="store_true",
        default=_env_bool("ALERT_EMAIL_USE_STARTTLS", True),
        help="Use STARTTLS when not using SSL (enabled by default).",
    )
    parser.add_argument(
        "--no-alert-email-use-starttls",
        action="store_false",
        dest="alert_email_use_starttls",
        help="Disable STARTTLS.",
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
    to_email: str
    from_email: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    use_ssl: bool
    use_starttls: bool
    subject_prefix: str
    levels: Set[str]

    def send(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_email
        msg["To"] = self.to_email
        msg.set_content(body)

        if self.use_ssl:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30) as smtp:
                if self.smtp_user:
                    smtp.login(self.smtp_user, self.smtp_password)
                smtp.send_message(msg)
            return

        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as smtp:
            if self.use_starttls:
                smtp.starttls()
            if self.smtp_user:
                smtp.login(self.smtp_user, self.smtp_password)
            smtp.send_message(msg)


def build_alert_mailer(args: argparse.Namespace, log: Callable[[str, str], None]) -> AlertMailer | None:
    to_email = (args.alert_email_to or "").strip()
    if not to_email:
        return None

    from_email = (args.alert_email_from or "").strip()
    smtp_host = (args.alert_email_smtp_host or "").strip()
    if not from_email or not smtp_host:
        log(
            "WARN",
            "Alert email disabled: --alert-email-to requires --alert-email-from and --alert-email-smtp-host.",
        )
        return None

    levels = parse_alert_levels(args.alert_levels or "")
    return AlertMailer(
        to_email=to_email,
        from_email=from_email,
        smtp_host=smtp_host,
        smtp_port=args.alert_email_smtp_port,
        smtp_user=(args.alert_email_smtp_user or "").strip(),
        smtp_password=args.alert_email_smtp_password or "",
        use_ssl=bool(args.alert_email_use_ssl),
        use_starttls=bool(args.alert_email_use_starttls),
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
