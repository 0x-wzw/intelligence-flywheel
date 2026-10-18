#!/usr/bin/env python3
"""STAGE 2 — Asset bot.

Reads videos/<project>/SCRIPT.json and generates:
  - one image per shot (Cloudflare FLUX) -> videos/<project>/assets/shot-NN.png
  - one narration WAV per shot (ILMU TTS) -> videos/<project>/assets/shot-NN.wav
  - a full narration WAV (all lines concatenated) -> videos/<project>/assets/narration.wav

Usage:
  video-assets.py <project>
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from video_media import _load_env, ensure_project, generate_image, generate_tts


def main():
    if len(sys.argv) < 2:
        print("usage: video-assets.py <project>")
        sys.exit(1)
    project = sys.argv[1]
    proj = ensure_project(project)
    script_path = os.path.join(proj, "SCRIPT.json")
    if not os.path.exists(script_path):
        print(f"❌ No SCRIPT.json in {proj}. Run video-script.py first.")
        sys.exit(1)
    with open(script_path) as f:
        script = json.load(f)

    assets = os.path.join(proj, "assets")
    os.makedirs(assets, exist_ok=True)

    shots = script.get("shots", [])
    wavs = []
    for shot in shots:
        idx = shot.get("index", 1)
        # image
        img = os.path.join(assets, f"shot-{idx:02d}.png")
        if not os.path.exists(img):
            print(f"  🖼  generating image for shot {idx}...")
            generate_image(shot.get("image_prompt", shot.get("visual", "abstract background")), img)
        # narration
        wav = os.path.join(assets, f"shot-{idx:02d}.wav")
        if not os.path.exists(wav):
            print(f"  🎙  generating narration for shot {idx}...")
            generate_tts(shot.get("narration", ""), wav)
        wavs.append(wav)

    # concatenate all shot narrations into one full narration
    full = os.path.join(assets, "narration.wav")
    if wavs and not os.path.exists(full):
        print("  🎬  concatenating narration...")
        # build concat list
        lst = os.path.join(assets, "concat.txt")
        with open(lst, "w") as f:
            for w in wavs:
                f.write(f"file '{os.path.abspath(w)}'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, full],
            check=True,
        )
        os.remove(lst)

    print(f"✅ Assets ready in {assets}")
    print(f"   {len(shots)} images + {len(wavs)} narration clips + full narration.wav")


if __name__ == "__main__":
    main()
