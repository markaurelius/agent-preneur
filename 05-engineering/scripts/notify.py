#!/usr/bin/env python3
"""
Agent ↔ WhatsApp communication helpers.

Sending:
    python scripts/notify.py "message" [--title T] [--priority high]

Reading inbox (free-form messages from user):
    python scripts/notify.py --inbox

Update status (shown when user texts "status"):
    python scripts/notify.py --write-status "Iter 26 | Brier 0.2341 | ..."

Check signal (CONTINUE / STOP / STOP_NOW / SKIP written by user via WhatsApp):
    python scripts/notify.py --read-signal   # prints signal and clears it; empty = no signal

Environment:
    WHATSAPP_URL  — Baileys bridge (default: http://localhost:3000)
    NTFY_TOPIC    — ntfy.sh fallback topic slug
"""
import argparse
import json
import os
import sys
import urllib.request

_URL = lambda: os.environ.get("WHATSAPP_URL", "http://localhost:3000")


# ── Outbound ────────────────────────────────────────────────────────────────

def _try_whatsapp(message: str, title: str) -> bool:
    text = f"*{title}*\n{message}" if title != "Stock Agent" else message
    try:
        body = json.dumps({"message": text}).encode()
        req = urllib.request.Request(
            f"{_URL()}/send",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _try_ntfy(message: str, title: str, priority: str) -> bool:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=message.encode(),
            headers={
                "Title": title.encode(),
                "Priority": priority.encode(),
                "Content-Type": b"text/plain",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def notify(message: str, title: str = "Stock Agent", priority: str = "default") -> None:
    if _try_whatsapp(message, title):
        return
    if _try_ntfy(message, title, priority):
        return
    print(f"[notify] all transports failed — {title}: {message}", file=sys.stderr)


# ── Status (user texts "status" → bridge reads this file and replies) ────────

def write_status(text: str) -> None:
    """Update the one-line status string the bridge returns on 'status' command."""
    try:
        body = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            f"{_URL()}/status",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass  # non-fatal — bridge may not be running yet


# ── Inbox (free-form messages from user for Claude to act on) ───────────────

def check_inbox() -> list[str]:
    """Return pending free-form messages from the user. Clears the queue."""
    try:
        req = urllib.request.Request(f"{_URL()}/inbox", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return [m["text"] for m in data.get("messages", [])]
    except Exception:
        return []


# ── Signal (control commands: CONTINUE / STOP / STOP_NOW / SKIP) ────────────

def read_signal() -> str | None:
    """Return the pending control signal (if any) and clear it."""
    try:
        req = urllib.request.Request(f"{_URL()}/signal", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return data.get("signal") or None
    except Exception:
        return None


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("message", nargs="*", help="Notification text to send")
    parser.add_argument("--title", default="Stock Agent")
    parser.add_argument("--priority", default="default",
                        choices=["min", "low", "default", "high", "urgent"])
    parser.add_argument("--inbox", action="store_true",
                        help="Print pending inbox messages and exit")
    parser.add_argument("--write-status", metavar="TEXT",
                        help="Update the status string returned on 'status' command")
    parser.add_argument("--read-signal", action="store_true",
                        help="Print pending control signal (CONTINUE/STOP/etc.) and exit")
    args = parser.parse_args()

    if args.inbox:
        for m in check_inbox():
            print(m)
        sys.exit(0)

    if args.write_status:
        write_status(args.write_status)
        sys.exit(0)

    if args.read_signal:
        sig = read_signal()
        if sig:
            print(sig)
        sys.exit(0)

    if not args.message:
        parser.error("message required (or use --inbox / --write-status / --read-signal)")

    notify(" ".join(args.message), title=args.title, priority=args.priority)
