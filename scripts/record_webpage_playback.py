#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
FFMPEG_ROOT = SKILL_DIR / "tools" / "ffmpeg"


def require_ffmpeg() -> str:
    local_candidates = list(FFMPEG_ROOT.rglob("ffmpeg.exe"))
    if local_candidates:
        return str(local_candidates[0])
    path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not path:
        raise RuntimeError("Missing ffmpeg. Run capability_probe.py or install ffmpeg first.")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record a webpage video playback from the desktop as a fallback when direct "
            "download/yt-dlp cannot access a protected webpage video."
        )
    )
    parser.add_argument("url", nargs="?", default="", help="Optional webpage URL to open before recording")
    parser.add_argument("--output", required=False, default="outputs/browser-playback-capture.mp4")
    parser.add_argument("--duration", type=float, default=60.0, help="Recording duration in seconds")
    parser.add_argument("--warmup", type=float, default=8.0, help="Seconds to wait after opening the URL")
    parser.add_argument("--framerate", type=int, default=12, help="Screen recording frame rate")
    parser.add_argument("--audio-device", default="", help='Optional dshow audio device, e.g. "Stereo Mix"')
    parser.add_argument("--list-devices", action="store_true", help="List DirectShow devices and exit")
    parser.add_argument("--no-open", action="store_true", help="Do not open the URL; record the current desktop")
    parser.add_argument("--draw-mouse", action="store_true", help="Include the mouse cursor in the capture")
    return parser.parse_args()


def list_devices(ffmpeg: str) -> int:
    cmd = [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    completed = subprocess.run(cmd, text=True)
    # ffmpeg returns non-zero after listing dummy devices; that is expected.
    return 0 if completed.returncode in {0, 1} else completed.returncode


def build_record_command(args: argparse.Namespace, ffmpeg: str, output_path: Path) -> list[str]:
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-f",
        "gdigrab",
        "-framerate",
        str(args.framerate),
        "-draw_mouse",
        "1" if args.draw_mouse else "0",
        "-i",
        "desktop",
    ]
    if args.audio_device:
        cmd.extend(["-f", "dshow", "-i", f"audio={args.audio_device}"])
    cmd.extend([
        "-t",
        str(max(1.0, args.duration)),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
    ])
    if args.audio_device:
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    else:
        cmd.extend(["-an"])
    cmd.append(str(output_path))
    return cmd


def main() -> int:
    args = parse_args()
    ffmpeg = require_ffmpeg()
    if args.list_devices:
        return list_devices(ffmpeg)

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.url and not args.no_open:
        webbrowser.open(args.url)
        time.sleep(max(0.0, args.warmup))

    cmd = build_record_command(args, ffmpeg, output_path)
    completed = subprocess.run(cmd)
    if completed.returncode != 0:
        return completed.returncode
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Recording produced an empty file: {output_path}")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
