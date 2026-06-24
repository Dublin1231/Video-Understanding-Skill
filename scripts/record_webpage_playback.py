#!/usr/bin/env python3
import argparse
import locale
import re
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
    parser.add_argument("--auto-audio", action="store_true", help="Try to auto-select a system playback/virtual audio device")
    parser.add_argument("--audio-required", action="store_true", help="Fail if no audio device is selected or auto-detected")
    parser.add_argument("--list-devices", action="store_true", help="List DirectShow devices and exit")
    parser.add_argument("--audio-help", action="store_true", help="Print Windows system-audio setup guidance and exit")
    parser.add_argument("--no-open", action="store_true", help="Do not open the URL; record the current desktop")
    parser.add_argument("--draw-mouse", action="store_true", help="Include the mouse cursor in the capture")
    return parser.parse_args()


def list_devices(ffmpeg: str) -> int:
    cmd = [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    completed = subprocess.run(cmd, text=True)
    # ffmpeg returns non-zero after listing dummy devices; that is expected.
    return 0 if completed.returncode in {0, 1} else completed.returncode


def get_dshow_devices(ffmpeg: str) -> list[tuple[str, str]]:
    cmd = [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    completed = subprocess.run(cmd, capture_output=True)
    encoding = locale.getpreferredencoding(False) or "utf-8"
    text = "\n".join(
        part.decode(encoding, errors="replace")
        for part in [completed.stdout, completed.stderr]
        if part
    )
    devices = []
    current_kind = ""
    for line in text.splitlines():
        if "(video)" in line:
            current_kind = "video"
        elif "(audio)" in line:
            current_kind = "audio"
        match = re.search(r'\]\s+"([^"]+)"\s+\((video|audio)\)', line)
        if match:
            devices.append((match.group(2), match.group(1)))
            current_kind = match.group(2)
            continue
        alt_match = re.search(r'\]\s+"([^"]+)"$', line)
        if alt_match and current_kind:
            devices.append((current_kind, alt_match.group(1)))
    return devices


def choose_audio_device(ffmpeg: str) -> str:
    audio_devices = [name for kind, name in get_dshow_devices(ffmpeg) if kind == "audio"]
    if not audio_devices:
        return ""

    preferred_patterns = [
        r"stereo mix",
        r"what u hear",
        r"loopback",
        r"speaker",
        r"speakers",
        "\u626c\u58f0\u5668",
        "\u7acb\u4f53\u58f0\u6df7\u97f3",
        "\u7cfb\u7edf\u58f0\u97f3",
        r"virtual",
        "\u865a\u62df",
    ]
    reject_patterns = [
        r"\bmic\b",
        r"microphone",
        "\u9ea6\u514b\u98ce",
        r"mic array",
    ]
    filtered_audio_devices = [
        name
        for name in audio_devices
        if not any(re.search(pattern, name, re.IGNORECASE) for pattern in reject_patterns)
    ]
    for pattern in preferred_patterns:
        for name in filtered_audio_devices:
            if re.search(pattern, name, re.IGNORECASE):
                return name
    return ""


def audio_setup_guidance() -> str:
    return "\n".join([
        "Audio setup guidance:",
        "- Windows Stereo Mix: Settings > System > Sound > More sound settings > Recording. Right-click the blank area, enable Show Disabled Devices, then enable Stereo Mix if it exists.",
        "- If Stereo Mix does not exist, install or enable a virtual audio cable / virtual sound card, then rerun --list-devices and pass the new device with --audio-device.",
        "- If you only see microphones, do not auto-select them for browser playback; they usually capture room noise, not system audio.",
        "- You can still record visuals only by removing --audio-required.",
    ])


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
    if args.audio_help:
        print(audio_setup_guidance())
        return 0
    if args.list_devices:
        return list_devices(ffmpeg)

    if not args.audio_device and args.auto_audio:
        args.audio_device = choose_audio_device(ffmpeg)
        if args.audio_device:
            print(f"Auto-selected audio device: {args.audio_device}", file=sys.stderr)
        else:
            print(
                "No system playback/loopback audio device was auto-detected; recording video only.",
                file=sys.stderr,
            )
            print(audio_setup_guidance(), file=sys.stderr)
    if args.audio_required and not args.audio_device:
        raise RuntimeError(
            "No audio device selected. Run with --list-devices, then pass --audio-device \"<device name>\", "
            "or omit --audio-required to record visuals only.\n"
            f"{audio_setup_guidance()}"
        )

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
