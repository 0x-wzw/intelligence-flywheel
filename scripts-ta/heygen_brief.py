#!/usr/bin/env python3
"""heygen_brief.py — render the daily RAG harness report as a HyperFrames
briefing video: TTS narration + synced captions + per-stage status chips.

Called as the harness's final stage (replaces the raw MP3 tts_summary when
HyperFrames is available; falls back to audio if rendering fails).

Usage:
    python3 heygen_brief.py <health-json> <out-mp4>

Output: 1080p MP4, dark mission-control theme, ~20-30s.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

HOME = Path.home()
PROJ = HOME / ".hermes" / "cache" / "heygen-brief"
VOICE = "en-US-AriaNeural"
EDGE = HOME / ".hermes/hermes-agent/venv/bin/edge-tts"

COMPOSITION = """<!DOCTYPE html>
<html>
<head>
<style>
  :root {{
    --bg: #0a0f1a; --accent: #22d3ee; --ok: #34d399; --fail: #f87171;
    --text: #e5e7eb; --dim: #6b7280;
  }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font-family: 'SF Mono', Menlo, monospace; overflow: hidden; }}
  .stage {{ width: 100%; height: 100%; display: flex; flex-direction: column;
            justify-content: center; gap: 34px; padding: 100px 80px;
            box-sizing: border-box; }}
  .header {{ display: flex; flex-direction: column; gap: 6px; }}
  .title {{ font-size: 52px; font-weight: 700; letter-spacing: 3px; }}
  .day {{ font-size: 26px; color: var(--accent); }}
  .status {{ font-size: 32px; font-weight: 700; padding: 12px 26px;
            border-radius: 8px; align-self: flex-start; }}
  .status.ok {{ background: rgba(52,211,153,.12); color: var(--ok);
               border: 1px solid var(--ok); }}
  .status.bad {{ background: rgba(248,113,113,.12); color: var(--fail);
                border: 1px solid var(--fail); }}
  .chips {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .chip {{ padding: 12px 18px; border-radius: 6px; font-size: 24px;
          border: 1px solid var(--dim); color: var(--dim); }}
  .chip.on {{ border-color: var(--ok); color: var(--ok);
             background: rgba(52,211,153,.08); }}
  .chip.off {{ border-color: var(--fail); color: var(--fail);
              background: rgba(248,113,113,.08); }}
  .metrics {{ display: flex; flex-direction: column; gap: 26px; }}
  .metric .k {{ color: var(--dim); font-size: 17px; text-transform: uppercase;
               letter-spacing: 2px; }}
  .metric .v {{ font-size: 52px; font-weight: 700; color: var(--accent); }}
  .caption-box {{ position: absolute; left: 50%; bottom: 110px;
                 transform: translateX(-50%); font-size: 32px;
                 background: rgba(17,24,39,.88); padding: 14px 32px;
                 border-radius: 10px; border: 1px solid #1f2937;
                 white-space: nowrap; max-width: 88%; overflow: hidden;
                 text-overflow: ellipsis; }}
</style>
</head>
<body>
  <div data-composition-id="root" data-width="1080" data-height="1920"
       data-start="0" data-duration="{duration}">
    <audio id="narr" src="narration.mp3" data-start="0"
           data-duration="{audio_dur}" data-track-index="9" data-volume="1"></audio>

    <div class="stage" style="position:relative;width:1080px;height:1920px;">
      <div class="header" id="hdr">
        <div class="title">RAG PIPELINE REPORT</div>
        <div class="day">{day}</div>
      </div>
      <div class="status {status_cls}" id="status">{status_text}</div>
      <div class="chips" id="chips">{chips_html}</div>
      <div class="metrics" id="metrics">{metrics_html}</div>
      <div id="caption-root">{captions_html}</div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <script>
    window.__timelines = window.__timelines || {{}};
    const tl = gsap.timeline({{ paused: true }});
    tl.from("#hdr", {{ opacity: 0, y: -30, duration: 0.5 }}, 0.1);
    tl.from("#status", {{ opacity: 0, y: 20, duration: 0.5 }}, 0.4);
    tl.from("#chips .chip", {{ opacity: 0, y: 20, stagger: 0.12, duration: 0.4 }}, 0.7);
    tl.from("#metrics .metric", {{ opacity: 0, y: 24, stagger: 0.15, duration: 0.45 }}, 1.1);
{caption_tls}
    window.__timelines["root"] = tl;
  </script>
</body>
</html>
"""


def _caption_blocks(caps: list[dict]) -> tuple[str, str]:
    """Build static caption divs + their deterministic GSAP cue lines.
    Every group gets a hard autoAlpha kill after its exit (per skill rules)."""
    html, tls = [], []
    for i, c in enumerate(caps):
        html.append(
            f'<div class="caption-box" id="cap-{i}">{c["text"]}</div>')
        tls.append(
            f'    tl.set("#cap-{i}", {{ autoAlpha: 0 }}, 0);')
        tls.append(
            f'    tl.to("#cap-{i}", {{ autoAlpha: 1, duration: 0.15 }}, {c["start"]});')
        tls.append(
            f'    tl.set("#cap-{i}", {{ autoAlpha: 0 }}, {c["end"]});')
    return "\n".join(html), "\n".join(tls)


def build_spoken(d: dict) -> tuple[str, list[tuple[str, bool]]]:
    v = d.get("verify", {})
    sm = d.get("smoke", {})
    cap = d.get("capture", {})
    dur = d.get("duration_s", 0.0)
    ok = d.get("status") == "ok"
    chunks = v.get("chunks", 0)
    orphans = v.get("orphans", 0)
    spoken = (
        f"Daily RAG pipeline report. Six stages completed in {dur:g} seconds. "
        f"{chunks} chunks indexed, {orphans} orphans detected. "
        f"Retrieval probes: {sm.get('hit', 0)} hits, {sm.get('miss', 0)} misses. "
        f"Vault captures: {cap.get('written', 0)} written, "
        f"{cap.get('skipped', 0)} already current. "
        + ("All stages green." if ok else "Attention required — check the health log.")
    )
    chips = [
        ("EXTRACT", True),
        ("INGEST", d.get("ingest", {}).get("docstore_chunks", 0) > 0),
        ("VERIFY", v.get("pass", False)),
        ("SMOKE", sm.get("hit", 0) >= sm.get("miss", 0)),
        ("CAPTURE", ok),
    ]
    return spoken, chips


def main() -> int:
    health_path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        HOME / ".hermes/logs/rag-ingest-health.json")
    out_mp4 = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        HOME / ".hermes/logs/heygen-brief.mp4")

    d = json.loads(health_path.read_text()) if health_path.exists() else {}
    day = str(d.get("started", date.today().isoformat()))[:10]
    ok = d.get("status") == "ok"

    spoken, chips = build_spoken(d)
    print(f"[brief] narration: {spoken[:100]}...")

    PROJ.mkdir(parents=True, exist_ok=True)

    # 1. narration audio
    audio = PROJ / "narration.mp3"
    edge = EDGE if EDGE.exists() else shutil.which("edge-tts")
    if not edge:
        print("[brief] edge-tts not found; aborting video")
        return 2
    r = subprocess.run([str(edge), "--text", spoken, "--voice", VOICE,
                        "--write-media", str(audio)],
                       capture_output=True, timeout=90)
    if r.returncode != 0:
        print(f"[brief] TTS failed: {r.stderr.decode()[-150:]}")
        return 2
    print(f"[brief] narration synthesized: {audio.stat().st_size} bytes")

    # 2. composition html
    chips_html = "".join(
        f'<div class="chip {"on" if on else "off"}">{name}</div>'
        for name, on in chips)
    metrics = [
        ("CHUNKS", d.get("verify", {}).get("chunks", 0)),
        ("ORPHANS", d.get("verify", {}).get("orphans", 0)),
        ("PROBE HITS", f"{d.get('smoke', {}).get('hit', 0)}/{d.get('smoke', {}).get('hit', 0) + d.get('smoke', {}).get('miss', 0)}"),
        ("CAPTURES", f"{d.get('capture', {}).get('written', 0)}w/{d.get('capture', {}).get('skipped', 0)}s"),
    ]
    metrics_html = "".join(
        f'<div class="metric"><div class="k">{k}</div><div class="v">{v}</div></div>'
        for k, v in metrics)

    # audio duration via ffprobe
    try:
        audio_dur = float(subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(audio)],
            capture_output=True, text=True).stdout.strip())
    except Exception:  # noqa: BLE001
        audio_dur = 30.0
    duration = round(audio_dur + 0.5, 2)

    # captions -> static divs + gsap cues
    captions_html, caption_tls = "", ""
    cap_file = PROJ / "captions.json"
    if cap_file.exists():
        try:
            caps = json.loads(cap_file.read_text())
            captions_html, caption_tls = _caption_blocks(caps)
        except Exception as e:  # noqa: BLE001
            print(f"[brief] captions parse failed: {e}")

    html = COMPOSITION.format(
        duration=duration,
        audio_dur=round(audio_dur, 2),
        day=day,
        status_text="ALL STAGES GREEN" if ok else "ATTENTION REQUIRED",
        status_cls="ok" if ok else "bad",
        chips_html=chips_html,
        metrics_html=metrics_html,
        captions_html=captions_html,
        caption_tls=caption_tls,
    )
    (PROJ / "index.html").write_text(html)

    # 3. captions from narration (word-level via faster-whisper timestamps);
    #    prefer the hermes-agent venv whose python has faster_whisper
    cap_script = """
import json, sys
import faster_whisper
model = faster_whisper.WhisperModel('base', device='cpu', compute_type='int8')
segments, _ = model.transcribe(sys.argv[1], word_timestamps=True)
caps = []
buf, start = [], 0.0
for seg in segments:
    for w in (seg.words or []):
        buf.append(w.word.strip())
        if len(buf) >= 6:
            caps.append({'start': round(start, 2), 'end': round(w.end, 2),
                         'text': ' '.join(buf)})
            buf, start = [], w.end
if buf and caps:
    caps.append({'start': start, 'end': caps[-1]['end'] + 1.2, 'text': ' '.join(buf)})
print(json.dumps(caps))
"""
    try:
        venv_py = HOME / ".hermes/hermes-agent/venv/bin/python"
        py = str(venv_py) if venv_py.exists() else sys.executable
        r = subprocess.run([py, "-c", cap_script, str(audio)],
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-200:])
        caps = json.loads(r.stdout)
        (PROJ / "captions.json").write_text(json.dumps(caps, indent=1))
        print(f"[brief] captions: {len(caps)} cues")
    except Exception as e:  # noqa: BLE001
        print(f"[brief] caption generation failed (continuing without): {e}")

    # 4. render
    r = subprocess.run(
        ["npx", "hyperframes", "lint"], cwd=PROJ, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[brief] lint failed:\n{r.stdout[-600:]}")
        return 2
    r = subprocess.run(
        ["npx", "hyperframes", "render", "--output", str(out_mp4),
         "--quality", "standard", "--fps", "30", "--format", "mp4"],
        cwd=PROJ, capture_output=True, text=True, timeout=600)
    if r.returncode == 0 and out_mp4.exists():
        print(f"[brief] rendered: {out_mp4} ({out_mp4.stat().st_size} bytes)")
        return 0
    print(f"[brief] render failed:\n{(r.stdout + r.stderr)[-600:]}")
    return 2


if __name__ == "__main__":
    sys.exit(main())