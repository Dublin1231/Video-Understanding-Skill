#!/usr/bin/env python3
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = SKILL_DIR / "vendor"
TOOLS_BIN_DIR = SKILL_DIR / "tools" / "ffmpeg" / "ffmpeg-8.1.1-full_build" / "bin"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))


def has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def find_binary(name: str) -> str | None:
    local_candidate = TOOLS_BIN_DIR / f"{name}.exe"
    if local_candidate.exists():
        return str(local_candidate)
    return shutil.which(name)


def main() -> int:
    report = {
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
        },
        "node": shutil.which("node"),
        "ffmpeg": find_binary("ffmpeg"),
        "ffprobe": find_binary("ffprobe"),
        "env": {
            "OPENAI_API_KEY": bool(os.environ.get("OPENAI_API_KEY")),
            "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL") or None,
        },
        "python_modules": {
            "openai": has_module("openai"),
            "yaml": has_module("yaml"),
            "cv2": has_module("cv2"),
            "whisper": has_module("whisper"),
            "pytesseract": has_module("pytesseract"),
        },
    }

    report["recommended_path"] = recommend_path(report)
    print(json.dumps(report, indent=2))
    return 0


def recommend_path(report: dict) -> str:
    has_api = report["env"]["OPENAI_API_KEY"]
    has_openai_sdk = report["python_modules"]["openai"]
    has_ffmpeg = bool(report["ffmpeg"])
    has_ffprobe = bool(report["ffprobe"])
    has_cv = report["python_modules"]["cv2"]
    has_whisper = report["python_modules"]["whisper"]

    if has_api and has_openai_sdk and has_ffmpeg and has_ffprobe:
        return "openai-hybrid"
    if has_api and has_openai_sdk:
        return "native-openai-or-hybrid"
    if has_ffmpeg and (has_cv or has_whisper):
        return "timeline-pipeline"
    if has_ffmpeg:
        return "transcript-and-frame-pipeline-with-external-tools"
    return "dependency-blocked"


if __name__ == "__main__":
    raise SystemExit(main())
