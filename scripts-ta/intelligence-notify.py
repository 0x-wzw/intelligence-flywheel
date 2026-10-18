#!/usr/bin/env python3
"""intelligence-notify.py — share the distilled-insights link to Telegram + Lark.

Final leg of the intelligence flywheel (2026-10-08):

  vault (raw) → Notion (distilled, via intelligence-publish.py) → THIS → Telegram + Lark

Reads the Notion page URL written by intelligence-publish.py
(~/.hermes/logs/notion-daily-page-url.txt) plus its health file for the day's
stats, composes ONE compact message, and posts it to:
  - Telegram home channel   (TELEGRAM_BOT_TOKEN + TELEGRAM_HOME_CHANNEL)
  - Lark/Feishu home chat   (FEISHU_APP_ID/SECRET, FEISHU_HOME_CHANNEL)

Runs daily at 08:20 (after intelligence-publish at 08:15). Safe to re-run:
sends the current link again; never duplicates content (link-only share).
"""
import json
import os
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

ENV_PATH = Path.home() / ".hermes/.env"
URL_FILE = Path.home() / ".hermes/logs/notion-daily-page-url.txt"
HEALTH_FILE = Path("/Users/admin/Documents/Obsidian Vault/System/intelligence-publish-health.json")
DASHBOARD = "https://intelligence.teoh.my/"


def load_env():
    vals = {}
    try:
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return vals


def http_post_json(url, body, headers=None, timeout=25):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def compose_message(day):
    notion_url = URL_FILE.read_text().strip() if URL_FILE.exists() else ""
    health = {}
    try:
        health = json.loads(HEALTH_FILE.read_text())
    except Exception:
        pass
    if health.get("day") != day:  # stale health — don't quote its numbers
        health = {}
    n = health.get("total_articles")
    c = health.get("total_categories")
    stats = f"{n} signals across {c} verticals" if n and c else "today's distilled insights"
    lines = [
        f"🧠 Daily Intelligence — {day}",
        f"Distilled: {stats}",
        f"🔗 Notion: {notion_url or '(link unavailable — check intelligence-publish)'}",
        f"🌐 Dashboard: {DASHBOARD}",
        "🗂 Raw captures: Obsidian vault → Knowledge/00-Raw/",
    ]
    return "\n".join(lines), notion_url


def send_telegram(token, chat_id, text):
    try:
        r = http_post_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": False})
        ok = bool(r.get("ok"))
        print(f"  {'✅' if ok else '⚠'} Telegram: {'sent' if ok else r.get('description', 'unknown error')}")
        return ok
    except Exception as e:
        print(f"  ⚠ Telegram failed: {e}")
        return False


def send_lark(app_id, app_secret, chat_id, text, domain="lark"):
    host = "open.larksuite.com" if domain == "lark" else "open.feishu.cn"
    try:
        tok = http_post_json(
            f"https://{host}/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": app_id, "app_secret": app_secret})
        access = tok.get("tenant_access_token")
        if not access:
            print(f"  ⚠ Lark token failed: {tok.get('msg')}")
            return False
        r = http_post_json(
            f"https://{host}/open-apis/im/v1/messages?receive_id_type=chat_id",
            {"receive_id": chat_id, "msg_type": "text",
             "content": json.dumps({"text": text})},
            headers={"Authorization": f"Bearer {access}"})
        ok = r.get("code") == 0
        print(f"  {'✅' if ok else '⚠'} Lark: {'sent' if ok else r.get('msg')}")

        return ok
    except Exception as e:
        print(f"  ⚠ Lark failed: {e}")
        return False


def update_health_flag():
    try:
        health = json.loads(HEALTH_FILE.read_text())
        health["notified"] = True
        health["notified_at"] = datetime.now().isoformat()
        HEALTH_FILE.write_text(json.dumps(health, indent=2))
    except Exception:
        pass


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    env = load_env()
    text, notion_url = compose_message(day)
    print(f"📣 Intelligence link share — {day}")
    print(text)
    print()

    tg_token = env.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = env.get("TELEGRAM_HOME_CHANNEL", "")
    fs_id = env.get("FEISHU_APP_ID", "")
    fs_secret = env.get("FEISHU_APP_SECRET", "")
    fs_chat = env.get("FEISHU_HOME_CHANNEL", "")
    fs_domain = env.get("FEISHU_DOMAIN", "lark")

    results = {}
    if tg_token and tg_chat:
        results["telegram"] = send_telegram(tg_token, tg_chat, text)
    else:
        print("  ⚠ Telegram skipped (no token/chat in env)")
    if fs_id and fs_secret and fs_chat:
        results["lark"] = send_lark(fs_id, fs_secret, fs_chat, text, fs_domain)
    else:
        print("  ⚠ Lark skipped (no app credentials/chat in env)")

    if any(results.values()):
        update_health_flag()
    ok = any(results.values())
    Path.home().joinpath(".hermes/logs/intelligence-notify.log").open("a").write(
        f"{datetime.now().isoformat()} | {day} | notion={bool(notion_url)} | {results}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
