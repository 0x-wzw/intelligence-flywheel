#!/usr/bin/env python3
"""Shared media helpers for the video production pipeline.

Provides:
  - generate_image(prompt, out_path)  -> Cloudflare Workers AI (FLUX)
  - generate_tts(text, out_path)      -> ILMU ilmu-tts-v2 (48kHz PCM -> wav)
  - ensure_project(name)              -> creates videos/<name>/ in the vault
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

VAULT = os.path.expanduser("~/Documents/Obsidian Vault")
VIDEOS = os.path.join(VAULT, "videos")


def _load_env():
    """Load ~/.hermes/.env into os.environ (without overriding existing)."""
    env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v


def generate_image(prompt, out_path, model="@cf/black-forest-labs/flux-1-schnell"):
    """Generate an image via Cloudflare Workers AI. Returns out_path or raises."""
    _load_env()
    acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    tok = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not acct or not tok:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"prompt": prompt}).encode(),
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    if not data.get("success"):
        raise RuntimeError(f"CF image gen failed: {data.get('errors')}")
    img = data["result"]["image"]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(img))
    return out_path


def generate_tts(text, out_path, voice="voice_1", model="ilmu-tts-v2"):
    """Generate narration via ILMU TTS. Returns a 48kHz mono WAV path."""
    _load_env()
    key = os.environ.get("ILMU_API_KEY")
    if not key:
        raise RuntimeError("ILMU_API_KEY not set")
    url = "https://api.ilmu.ai/v1/audio/speech"
    req = urllib.request.Request(
        url,
        data=json.dumps({"model": model, "input": text, "voice": voice}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        pcm = r.read()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    raw = out_path + ".raw"
    with open(raw, "wb") as f:
        f.write(pcm)
    # Convert raw 48kHz mono s16le PCM to WAV
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", "48000", "-ac", "1",
         "-i", raw, out_path],
        check=True,
    )
    os.remove(raw)
    return out_path


def ensure_project(name):
    """Create videos/<name>/ in the vault. Returns the project dir."""
    proj = os.path.join(VIDEOS, name)
    os.makedirs(proj, exist_ok=True)
    return proj


if __name__ == "__main__":
    # quick self-test
    _load_env()
    print("media helpers loaded OK")
