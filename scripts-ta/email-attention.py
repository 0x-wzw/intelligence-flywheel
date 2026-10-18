#!/usr/bin/env python3
"""Email attention checker — checks the Lark mailbox for unread emails that
likely need the user's attention, and prints a concise alert summary.

Runs on a schedule via Hermes cron (no-agent mode). stdout is delivered to
Telegram. If nothing needs attention, prints [SILENT] so no message is sent.

Heuristic for "needs attention":
  - Unread email in INBOX
  - NOT from a known newsletter/marketing/notification sender
  - OR carries an IMPORTANT / FLAGGED label
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

# Senders/domains that are almost always automated and don't need attention.
NOISE_PATTERNS = [
    r"luma-mail\.com$", r"beehiiv\.com$", r"substack\.com$",
    r"calendar\.luma-mail\.com$", r"@mail\.", r"noreply", r"no-reply",
    r"donotreply", r"@accounts\.google\.com$", r"@cloudjoi\.com$",
    r"@jotform\.com$", r"@mailchimpapp\.com$", r"@seagm\.com$",
    r"@xcard\.my$", r"@academy\.itu\.int$", r"@luma\.com$",
    r"@e\.airwallex\.com$", r"@alliwan\.com$", r"@maicnexus\.com$",
    r"@verithos\.co$", r"@mysdg\.com\.my$",
]

# Labels that signal importance regardless of sender.
IMPORTANT_LABELS = {"IMPORTANT", "FLAGGED"}


def run_lark(args):
    """Run a lark-cli command and return parsed JSON (or None on failure)."""
    env = dict(os.environ)
    env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
    try:
        proc = subprocess.run(
            ["lark-cli"] + args,
            capture_output=True, text=True, timeout=60, env=env,
        )
        out = proc.stdout
        # Strip the leading "tip:" line if present.
        if out.lstrip().startswith("tip:"):
            out = out[out.find("\n") + 1:]
        return json.loads(out)
    except Exception:
        return None


def is_noise(from_str, subject, labels):
    """Return True if the email is automated noise that doesn't need attention."""
    if any(lbl in IMPORTANT_LABELS for lbl in labels):
        return False
    # Extract the email address from "Name <addr>" form.
    m = re.search(r"<([^>]+)>", from_str)
    addr = (m.group(1) if m else from_str).lower()
    for pat in NOISE_PATTERNS:
        if re.search(pat, addr):
            return True
    return False


def main():
    data = run_lark(["mail", "+triage", "--is-unread", "--labels", "--max", "100", "--json"])
    if not data or "messages" not in data:
        print("[SILENT]")
        return 0

    messages = data.get("messages", [])
    attention = []
    for m in messages:
        labels = [l.strip() for l in m.get("labels", "").split(",") if l.strip()]
        if not is_noise(m.get("from", ""), m.get("subject", ""), labels):
            attention.append(m)

    if not attention:
        print("[SILENT]")
        return 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"📬 {len(attention)} email(s) need your attention ({now})", ""]
    for m in attention[:15]:
        lines.append(f"• {m.get('from', '?')}")
        lines.append(f"  {m.get('subject', '?')}")
        lines.append(f"  [{m.get('date', '?')}]")
        lines.append("")
    if len(attention) > 15:
        lines.append(f"...and {len(attention) - 15} more")
    print("\n".join(lines).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
