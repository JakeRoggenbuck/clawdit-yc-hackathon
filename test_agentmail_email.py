#!/usr/bin/env python3
import argparse
import os

from agentmail import AgentMail
from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a simple AgentMail test email.")
    parser.add_argument("--to", default=os.environ.get("ADMIN_EMAIL", ""))
    parser.add_argument("--subject", default="AgentMail Test")
    parser.add_argument("--text", default="This is a test email from make-docs.")
    parser.add_argument(
        "--inbox-id",
        default=os.environ.get("AGENTMAIL_INBOX_ID", "gracefulbird586@agentmail.to"),
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    api_key = os.environ.get("AGENTMAIL_API_KEY", "").strip()
    to_email = (args.to or "").strip()
    inbox_id = (args.inbox_id or "").strip()

    if not api_key:
        print("Missing AGENTMAIL_API_KEY")
        return 2
    if not to_email:
        print("Missing recipient: set --to or ADMIN_EMAIL")
        return 2
    if not inbox_id:
        print("Missing inbox id: set --inbox-id or AGENTMAIL_INBOX_ID")
        return 2

    client = AgentMail(api_key=api_key)
    response = client.inboxes.messages.send(
        inbox_id=inbox_id,
        to=[to_email],
        subject=args.subject,
        text=args.text,
    )
    print(f"Email sent. Message ID: {response.message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
