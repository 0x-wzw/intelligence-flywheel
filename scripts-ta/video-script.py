#!/usr/bin/env python3
"""STAGE 1 — Script bot (premium LLM).

Takes a topic, uses a best-in-class model (nemotron-3-super on ollama-cloud)
to write a short narration script + shot list, and saves it to the vault as
videos/<project>/SCRIPT.json for the downstream asset + render stages.

Usage:
  video-script.py "<topic>" [project_name]
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from video_media import _load_env, ensure_project

MODEL = os.environ.get("VIDEO_SCRIPT_MODEL", "nemotron-3-super")
BASE = "https://ollama.com/v1"


def call_llm(prompt, model=MODEL):
    _load_env()
    key = os.environ.get("OLLAMA_API_KEY")
    if not key:
        raise RuntimeError("OLLAMA_API_KEY not set")
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": (
                    "You are a video scriptwriter. Given a topic, produce a short "
                    "faceless explainer script. Return STRICT JSON only, no markdown, "
                    "with this shape:\n"
                    "{\n"
                    "  \"title\": \"...\",\n"
                    "  \"duration_seconds\": 20,\n"
                    "  \"narration\": \"Full narration text, 2-4 sentences.\",\n"
                    "  \"shots\": [\n"
                    "    {\"index\": 1, \"duration\": 5, \"visual\": \"describe the visual\", "
                    "\"image_prompt\": \"detailed image-gen prompt\", \"narration\": \"line 1\"}\n"
                    "  ]\n"
                    "}\n"
                    "Keep it 3-5 shots, total ~20s. image_prompt must be a self-contained "
                    "text-to-image prompt (no text in image)."
                )},
                {"role": "user", "content": f"Topic: {prompt}"},
            ],
            "temperature": 0.7,
        }).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
    content = data["choices"][0]["message"]["content"]
    # strip code fences if present
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        content = content.rsplit("```", 1)[0]
    return json.loads(content)


def main():
    if len(sys.argv) < 2:
        print("usage: video-script.py '<topic>' [project]")
        sys.exit(1)
    topic = sys.argv[1]
    project = sys.argv[2] if len(sys.argv) > 2 else "explainer"
    proj = ensure_project(project)
    script = call_llm(topic)
    out = os.path.join(proj, "SCRIPT.json")
    with open(out, "w") as f:
        json.dump(script, f, indent=2)
    print(f"✅ Script written: {out}")
    print(f"   Title: {script.get('title')}")
    print(f"   Duration: {script.get('duration_seconds')}s, {len(script.get('shots', []))} shots")
    print(f"   Model: {MODEL}")


if __name__ == "__main__":
    main()
