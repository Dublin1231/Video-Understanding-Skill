#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a structured brief for video-understanding analysis."
    )
    parser.add_argument("video", help="Path to the source video")
    parser.add_argument("--question", default="", help="User question to answer")
    parser.add_argument(
        "--transcript",
        default="",
        help="Optional path to transcript/subtitle text",
    )
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "native", "timeline"],
        help="Preferred analysis mode",
    )
    args = parser.parse_args()

    video_path = Path(args.video).expanduser().resolve()
    transcript_path = (
        Path(args.transcript).expanduser().resolve() if args.transcript else None
    )

    brief = {
        "video_path": str(video_path),
        "video_exists": video_path.exists(),
        "transcript_path": str(transcript_path) if transcript_path else None,
        "transcript_exists": bool(transcript_path and transcript_path.exists()),
        "question": args.question or "Summarize what is said and shown over time.",
        "mode": args.mode,
        "required_output": [
            "one_paragraph_summary",
            "timeline",
            "key_spoken_points",
            "key_visual_events",
            "visible_text",
            "uncertainties",
        ],
    }

    print(json.dumps(brief, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
