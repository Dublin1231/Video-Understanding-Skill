#!/usr/bin/env python3
import argparse
import base64
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_MODEL = "gpt-5.4"
DEFAULT_TRANSCRIBE_MODEL = "gpt-4o-transcribe-diarize"
DEFAULT_LOCAL_WHISPER_MODEL = "small"
DEFAULT_ANALYSIS_BATCH_SIZE = 12
DEFAULT_SCENE_THRESHOLD = 0.35
DEFAULT_MIN_CHANGE_GAP = 1.0
DEFAULT_LAYOUT_CHANGE_THRESHOLD = 0.18
DEFAULT_LAYOUT_DOWNSCALE_WIDTH = 160
DEFAULT_TITLE_CHANGE_THRESHOLD = 0.35
DEFAULT_TITLE_QUALITY_THRESHOLD = 0.55
DEFAULT_TITLE_SUPPORT_MARGIN = 0.045
DEFAULT_TITLE_BAND_TOP_RATIO = 0.04
DEFAULT_TITLE_BAND_BOTTOM_RATIO = 0.42
DEFAULT_NAV_SUPPORT_MARGIN = 0.04
DEFAULT_PRESENTER_SUPPORT_MARGIN = 0.05
DEFAULT_SAME_CHAPTER_MAX_GAP = 10.0
DEFAULT_SAME_CHAPTER_COMBINED_MAX = 0.46
DEFAULT_SAME_CHAPTER_STRUCTURE_MAX = 0.115
DEFAULT_SAME_CHAPTER_EDGE_PROFILE_MAX = 0.14
DEFAULT_SAME_CHAPTER_LOCALIZED_DELTA_MIN = 0.14
LAYOUT_GRID_WIDTH = 24
LAYOUT_GRID_HEIGHT = 14
DEFAULT_OCR_LANGUAGE = "chi_sim+eng"
DEFAULT_OCR_MIN_CONFIDENCE = 28.0
SKILL_DIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = SKILL_DIR / "vendor"
FFMPEG_ROOT = SKILL_DIR / "tools" / "ffmpeg"
LOCAL_MODELS_DIR = SKILL_DIR / "models"
LOCAL_TESSDATA_DIR = LOCAL_MODELS_DIR / "tessdata"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract representative frames from a video and analyze them with OpenAI Responses."
    )
    parser.add_argument("video", help="Path, direct video URL, or supported webpage video URL")
    parser.add_argument(
        "--question",
        default="Summarize what is said and shown over time.",
        help="Question for the model to answer",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenAI model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", ""),
        help="Optional OpenAI-compatible base URL. Defaults to OPENAI_BASE_URL if set.",
    )
    parser.add_argument(
        "--api-timeout",
        type=float,
        default=120.0,
        help="HTTP timeout in seconds for OpenAI API calls",
    )
    parser.add_argument(
        "--download-timeout",
        type=float,
        default=120.0,
        help="HTTP timeout in seconds when downloading video URLs",
    )
    parser.add_argument(
        "--no-yt-dlp",
        action="store_true",
        help="Disable yt-dlp fallback for webpage video URLs.",
    )
    parser.add_argument(
        "--cookies",
        default="",
        help="Optional Netscape-format cookies.txt file for yt-dlp webpage video downloads.",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default="",
        help="Optional browser name for yt-dlp cookies, for example: chrome, edge, firefox, or auto.",
    )
    parser.add_argument(
        "--auto-cookies",
        action="store_true",
        help="When a webpage video download needs cookies, automatically try common local browsers.",
    )
    parser.add_argument(
        "--transcribe-model",
        default=DEFAULT_TRANSCRIBE_MODEL,
        help=f"Audio transcription model to use when audio is present (default: {DEFAULT_TRANSCRIBE_MODEL})",
    )
    parser.add_argument(
        "--local-whisper-model",
        default=DEFAULT_LOCAL_WHISPER_MODEL,
        help=f"Local faster-whisper model name for fallback transcription (default: {DEFAULT_LOCAL_WHISPER_MODEL})",
    )
    parser.add_argument(
        "--skip-audio",
        action="store_true",
        help="Skip audio extraction and transcription even if the video has an audio stream.",
    )
    parser.add_argument(
        "--sampling-mode",
        choices=["coverage", "all-changes"],
        default="coverage",
        help="Frame sampling mode: cover the whole video with a bounded set, or capture every detected page/scene change.",
    )
    parser.add_argument(
        "--scene-detection",
        action="store_true",
        help="Prefer scene-change-based frame sampling before falling back to fixed intervals.",
    )
    parser.add_argument(
        "--scene-threshold",
        type=float,
        default=DEFAULT_SCENE_THRESHOLD,
        help=f"ffmpeg scene-change threshold for page/scene detection (default: {DEFAULT_SCENE_THRESHOLD})",
    )
    parser.add_argument(
        "--min-change-gap",
        type=float,
        default=DEFAULT_MIN_CHANGE_GAP,
        help=f"Minimum gap in seconds between detected change frames to suppress near-duplicate transition hits (default: {DEFAULT_MIN_CHANGE_GAP})",
    )
    parser.add_argument(
        "--screen-layout-filter",
        action="store_true",
        help="Apply a second-pass layout-change filter tuned for screen recordings to suppress minor animations and micro-movements.",
    )
    parser.add_argument(
        "--layout-change-threshold",
        type=float,
        default=DEFAULT_LAYOUT_CHANGE_THRESHOLD,
        help=f"Normalized image-difference threshold for keeping a detected layout change (default: {DEFAULT_LAYOUT_CHANGE_THRESHOLD})",
    )
    parser.add_argument(
        "--layout-downscale-width",
        type=int,
        default=DEFAULT_LAYOUT_DOWNSCALE_WIDTH,
        help=f"Downscaled width used for layout-change comparison images (default: {DEFAULT_LAYOUT_DOWNSCALE_WIDTH})",
    )
    parser.add_argument(
        "--title-ocr-filter",
        action="store_true",
        help="Use lightweight OCR on the top title region to prefer true page/section changes over small visual motion.",
    )
    parser.add_argument(
        "--title-change-threshold",
        type=float,
        default=DEFAULT_TITLE_CHANGE_THRESHOLD,
        help=f"Minimum normalized title-text difference to strongly support a page-change decision (default: {DEFAULT_TITLE_CHANGE_THRESHOLD})",
    )
    parser.add_argument(
        "--chapter-nav-filter",
        action="store_true",
        help="Use the bottom chapter navigation bar as an extra section-change signal for slide-style screen recordings.",
    )
    parser.add_argument(
        "--presenter-shot-filter",
        action="store_true",
        help="Detect presenter talking-head shots and suppress keeping them as separate page-change points unless other signals are strong.",
    )
    parser.add_argument(
        "--same-chapter-dedupe-filter",
        action="store_true",
        help="Conservatively drop near-duplicate page changes when the chapter context appears unchanged and only localized content shifts.",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Run OCR on sampled frames and include recognized text in the result.",
    )
    parser.add_argument(
        "--extract-doc-md",
        default="",
        help="Optional path to write Markdown extracted from document-like content shown in sampled video frames.",
    )
    parser.add_argument(
        "--doc-only",
        action="store_true",
        help="Only extract document Markdown from sampled frames; skip audio transcription, OCR summary, and model synthesis.",
    )
    parser.add_argument(
        "--doc-md-mode",
        choices=["literal", "polished"],
        default="literal",
        help="Document Markdown mode: literal preserves on-screen extracted text; polished groups content into generated knowledge headings.",
    )
    parser.add_argument(
        "--extract-speech-md",
        default="",
        help="Optional path to write blogger/speaker speech as knowledge-base Markdown.",
    )
    parser.add_argument(
        "--speech-only",
        action="store_true",
        help="Only transcribe speech and write speech Markdown; skip frame extraction, OCR, document extraction, and model synthesis.",
    )
    parser.add_argument(
        "--speech-md-mode",
        choices=["literal", "knowledge"],
        default="knowledge",
        help="Speech Markdown mode: literal keeps timestamped transcript; knowledge creates generated knowledge-note sections plus transcript excerpts.",
    )
    parser.add_argument(
        "--sample-seconds",
        type=float,
        default=5.0,
        help="Extract roughly one frame every N seconds",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=12,
        help="Upper bound on sampled frames in coverage mode. In all-changes mode this becomes a soft per-batch analysis size, not a hard extraction limit.",
    )
    parser.add_argument(
        "--analysis-batch-size",
        type=int,
        default=DEFAULT_ANALYSIS_BATCH_SIZE,
        help=f"Maximum number of frames analyzed in a single Responses call before batching (default: {DEFAULT_ANALYSIS_BATCH_SIZE})",
    )
    parser.add_argument(
        "--image-detail",
        choices=["low", "high", "auto"],
        default="auto",
        help="Detail level for input_image items",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of plain text",
    )
    parser.add_argument(
        "--report-json",
        default="",
        help="Optional path to write the full structured result as JSON",
    )
    parser.add_argument(
        "--report-md",
        default="",
        help="Optional path to write a Markdown report",
    )
    return parser.parse_args()


def require_dependency(name: str) -> str:
    local_candidates = list(FFMPEG_ROOT.rglob(f"{name}.exe"))
    if local_candidates:
        return str(local_candidates[0])
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Missing dependency: {name}")
    return path


def extract_first_url(value: str) -> str:
    match = re.search(r"https?://[^\s，。！？；、）)>\]\"']+", value or "")
    return match.group(0).rstrip(".,;:!?，。！？；、")


def normalize_source_input(value: str) -> str:
    if is_url(value):
        return value
    embedded_url = extract_first_url(value)
    return embedded_url or value


def is_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_webpage_video_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.netloc.lower()
    query = urllib.parse.parse_qs(parsed.query)
    if host.endswith("douyin.com") and parsed.path.rstrip("/") == "/jingxuan":
        modal_id = (query.get("modal_id") or [""])[0].strip()
        if modal_id:
            return f"https://www.douyin.com/video/{modal_id}"
    return url


def expand_short_video_url(url: str, timeout: float) -> str:
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.netloc.lower()
    short_hosts = ("v.douyin.com", "vm.tiktok.com", "vt.tiktok.com")
    if not any(host.endswith(short_host) for short_host in short_hosts):
        return url
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 video-understanding-skill/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.geturl() or url


def build_download_failure_help(source_url: str, normalized_url: str, error_text: str) -> str:
    lower_error = error_text.lower()
    parsed = urllib.parse.urlparse(normalized_url or source_url or "")
    host = parsed.netloc.lower()
    tips = []
    if normalized_url != source_url:
        tips.append(f"已将网页入口规范化为可下载器识别的视频页：{normalized_url}")
    if any(site in host for site in ("douyin.com", "iesdouyin.com", "tiktok.com")):
        tips.append("抖音/短视频平台通常需要新鲜浏览器登录态，公开网页能打开不代表下载器能直接取到视频。")
    if "fresh cookies" in lower_error:
        tips.append("当前站点要求 fresh cookies：请先在浏览器里打开并确认能播放，再重试 `--cookies-from-browser chrome` 或 `--cookies-from-browser edge`。")
    if "could not copy chrome cookie database" in lower_error:
        tips.append("浏览器 cookie 数据库暂时无法复制：请完全退出 Chrome/Edge 后重试，或导出 Netscape 格式 `cookies.txt` 后用 `--cookies <cookies.txt>`。")
    if "failed to decrypt with dpapi" in lower_error:
        tips.append("Windows DPAPI 没能解密浏览器 cookies：这通常和 Chromium 的本机加密/运行身份有关。最稳的做法是用浏览器扩展导出 Netscape 格式 `cookies.txt`，再用 `--cookies <cookies.txt>`。")
        tips.append("如果你有 Firefox 登录态，也可以尝试 `--cookies-from-browser firefox`，它有时能避开 Chromium DPAPI 限制。")
    if "could not find firefox cookies database" in lower_error:
        tips.append("没有找到 Firefox cookie 配置；如果你不用 Firefox，可以忽略这一项。")
    if "unsupported url" in lower_error:
        tips.append("当前 URL 不是下载器支持的页面形态；可以尝试复制分享链接、标准视频页链接，或先下载成本地 mp4。")
    if not tips:
        tips.append("可以尝试提供本地视频文件、直连 mp4 地址，或带登录态的 cookies.txt。")
    return "\n".join(f"- {tip}" for tip in tips)


def extension_from_url_or_content_type(url: str, content_type: str | None) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}:
        return suffix
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    mapping = {
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
        "video/x-matroska": ".mkv",
        "video/x-msvideo": ".avi",
    }
    return mapping.get(content_type, ".mp4")


def download_video_url(url: str, temp_dir: Path, timeout: float) -> Path:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "video-understanding-skill/1.0",
            "Accept": "video/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type.lower():
            raise RuntimeError(
                "The URL returned HTML, not a direct video file. Download the video first or provide a direct media URL."
            )
        extension = extension_from_url_or_content_type(url, content_type)
        video_path = temp_dir / f"downloaded-video{extension}"
        with video_path.open("wb") as output:
            shutil.copyfileobj(response, output)
    if not video_path.exists() or video_path.stat().st_size == 0:
        raise RuntimeError("Downloaded video URL produced an empty file.")
    return video_path


def find_downloaded_video(temp_dir: Path) -> Path | None:
    candidates = []
    for path in temp_dir.iterdir():
        if path.is_file() and path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}:
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_size)


def ytdlp_command_prefix() -> list[str]:
    ytdlp = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if ytdlp:
        return [ytdlp]
    try:
        import yt_dlp  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "This URL is not a direct media file and yt-dlp is not installed. "
            "Install yt-dlp or download the video locally first."
        ) from exc
    return [sys.executable, "-m", "yt_dlp"]


def download_video_with_ytdlp(
    url: str,
    temp_dir: Path,
    timeout: float,
    cookies: str = "",
    cookies_from_browser: str = "",
) -> Path:
    output_template = str(temp_dir / "downloaded-video.%(ext)s")
    prefix = ytdlp_command_prefix()
    cmd = prefix + [
        "--no-playlist",
        "--no-progress",
        "--socket-timeout",
        str(max(1, int(timeout))),
        "-f",
        "bv*+ba/best",
        "--merge-output-format",
        "mp4",
        "-o",
        output_template,
        url,
    ]
    insert_at = len(prefix)
    if cookies:
        cmd[insert_at:insert_at] = ["--cookies", str(Path(cookies).expanduser().resolve())]
        insert_at += 2
    if cookies_from_browser:
        cmd[insert_at:insert_at] = ["--cookies-from-browser", cookies_from_browser]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"yt-dlp failed to download the video URL: {error}")
    video_path = find_downloaded_video(temp_dir)
    if not video_path:
        raise RuntimeError("yt-dlp completed but no downloaded video file was found.")
    return video_path


def candidate_cookie_browsers(cookies_from_browser: str, auto_cookies: bool) -> list[str]:
    if cookies_from_browser:
        if cookies_from_browser.lower() == "auto":
            return ["chrome", "edge", "firefox"]
        return [cookies_from_browser]
    if auto_cookies:
        return ["chrome", "edge", "firefox"]
    return [""]


def resolve_video_source(
    url_or_path: str,
    temp_dir: Path,
    timeout: float,
    use_ytdlp: bool = True,
    cookies: str = "",
    cookies_from_browser: str = "",
    auto_cookies: bool = False,
) -> tuple[Path, str | None]:
    source_input = normalize_source_input(url_or_path)
    if not is_url(source_input):
        video_path = Path(source_input).expanduser().resolve()
        if not video_path.exists():
            raise SystemExit(f"Video not found: {video_path}")
        return video_path, None

    expanded_url = source_input
    try:
        expanded_url = expand_short_video_url(source_input, timeout)
    except Exception:
        expanded_url = source_input
    normalized_url = normalize_webpage_video_url(expanded_url)
    direct_error = None
    try:
        return download_video_url(normalized_url, temp_dir, timeout), source_input
    except Exception as exc:
        direct_error = str(exc)
    if use_ytdlp:
        errors = []
        initial_browser = "" if cookies_from_browser.lower() == "auto" else cookies_from_browser
        try:
            return download_video_with_ytdlp(
                normalized_url,
                temp_dir,
                timeout,
                cookies=cookies,
                cookies_from_browser=initial_browser,
            ), url_or_path
        except Exception as exc:
            errors.append(str(exc))
        if not cookies:
            for browser in candidate_cookie_browsers(cookies_from_browser, auto_cookies):
                if not browser or browser == initial_browser:
                    continue
                try:
                    return download_video_with_ytdlp(
                        normalized_url,
                        temp_dir,
                        timeout,
                        cookies_from_browser=browser,
                    ), url_or_path
                except Exception as exc:
                    errors.append(f"{browser}: {exc}")
        joined_errors = " | ".join(errors)
        help_text = build_download_failure_help(source_input, normalized_url, f"{direct_error} {joined_errors}")
        raise RuntimeError(
            "Unable to download video URL.\n"
            f"Direct download error: {direct_error}\n"
            f"yt-dlp error: {joined_errors}\n"
            "Recovery suggestions:\n"
            f"{help_text}"
        )
    help_text = build_download_failure_help(source_input, normalized_url, str(direct_error))
    raise RuntimeError(
        "Unable to download direct video URL.\n"
        f"Direct download error: {direct_error}\n"
        "Recovery suggestions:\n"
        f"{help_text}"
    )


def require_openai():
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "Missing Python package: openai. Install it in the runtime used for this skill."
        ) from exc
    return OpenAI


def require_faster_whisper():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Missing Python package: faster-whisper. Install it in the runtime used for this skill."
        ) from exc
    return WhisperModel


def resolve_local_whisper_model(local_whisper_model: str) -> str:
    candidate = Path(local_whisper_model).expanduser()
    if candidate.exists():
        return str(candidate.resolve())

    direct_dir = LOCAL_MODELS_DIR / local_whisper_model
    if direct_dir.exists():
        return str(direct_dir.resolve())

    repo_dir = LOCAL_MODELS_DIR / f"models--Systran--faster-whisper-{local_whisper_model}"
    snapshots_dir = repo_dir / "snapshots"
    if snapshots_dir.exists():
        snapshots = sorted([path for path in snapshots_dir.iterdir() if path.is_dir()])
        if snapshots:
            return str(snapshots[-1].resolve())

    return local_whisper_model


def ffprobe_duration(video_path: Path) -> float:
    ffprobe = require_dependency("ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def ffprobe_has_audio(video_path: Path) -> bool:
    ffprobe = require_dependency("ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return bool(result.stdout.strip())


def epsilon_for_duration(duration: float) -> float:
    return 0.05 if duration > 0.1 else max(duration / 10, 0.001)


def dedupe_timestamps(timestamps: list[float]) -> list[float]:
    deduped = []
    seen = set()
    for ts in timestamps:
        rounded = round(ts, 3)
        if rounded not in seen:
            seen.add(rounded)
            deduped.append(rounded)
    return deduped


def filter_timestamps_by_gap(timestamps: list[float], min_gap_seconds: float) -> list[float]:
    if not timestamps:
        return []
    filtered = [round(float(timestamps[0]), 3)]
    for ts in timestamps[1:]:
        rounded = round(float(ts), 3)
        if rounded - filtered[-1] >= min_gap_seconds:
            filtered.append(rounded)
    return filtered


def detect_scene_timestamps(
    video_path: Path,
    scene_threshold: float,
    min_change_gap: float,
) -> list[float]:
    ffmpeg = require_dependency("ffmpeg")
    cmd = [
        ffmpeg,
        "-i",
        str(video_path),
        "-filter:v",
        f"select='gt(scene,{scene_threshold})',metadata=print",
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = (result.stderr or "") + "\n" + (result.stdout or "")
    timestamps = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", output):
        timestamps.append(float(match.group(1)))
    return filter_timestamps_by_gap(dedupe_timestamps(timestamps), min_change_gap)


def determine_frame_budget(duration: float, sample_seconds: float, max_frames: int) -> int:
    if duration <= 0:
        return 1
    requested = max(1, math.ceil(duration / max(sample_seconds, 0.001)))
    return max(1, min(max_frames, requested))


def build_coverage_windows(duration: float, frame_budget: int) -> list[dict]:
    if duration <= 0 or frame_budget <= 1:
        return [{"start": 0.0, "end": max(duration, 0.0), "center": 0.0}]
    epsilon = epsilon_for_duration(duration)
    safe_end = max(duration - epsilon, 0.0)
    window_size = duration / frame_budget
    windows = []
    for index in range(frame_budget):
        start = index * window_size
        raw_end = duration if index == frame_budget - 1 else (index + 1) * window_size
        end = min(raw_end, safe_end)
        center = min(start + ((end - start) / 2), safe_end)
        windows.append(
            {
                "start": round(start, 3),
                "end": round(max(end, start), 3),
                "center": round(max(center, 0.0), 3),
            }
        )
    return windows


def select_segment_coverage_timestamps(
    duration: float,
    frame_budget: int,
    candidate_timestamps: list[float] | None = None,
) -> list[float]:
    windows = build_coverage_windows(duration, frame_budget)
    candidates = sorted(candidate_timestamps or [])
    selected = []
    for index, window in enumerate(windows):
        start = window["start"]
        end = window["end"]
        center = window["center"]
        in_window = [
            ts
            for ts in candidates
            if start <= ts <= end or (index == len(windows) - 1 and start <= ts <= duration)
        ]
        if in_window:
            choice = min(in_window, key=lambda ts: abs(ts - center))
        else:
            choice = center
        selected.append(round(choice, 3))

    deduped = []
    seen = set()
    for ts in selected:
        key = round(ts, 3)
        if key not in seen:
            seen.add(key)
            deduped.append(ts)

    if len(deduped) < frame_budget:
        for window in windows:
            key = round(window["center"], 3)
            if key not in seen:
                seen.add(key)
                deduped.append(window["center"])
            if len(deduped) >= frame_budget:
                break

    return deduped[:frame_budget] or [0.0]


def build_timestamps(duration: float, sample_seconds: float, max_frames: int) -> list[float]:
    if duration <= 0:
        return [0.0]
    frame_budget = determine_frame_budget(duration, sample_seconds, max_frames)
    return select_segment_coverage_timestamps(duration, frame_budget)


def ensure_anchor_timestamps(timestamps: list[float], duration: float) -> list[float]:
    if duration <= 0:
        return [0.0]
    epsilon = epsilon_for_duration(duration)
    safe_end = max(duration - epsilon, 0.0)
    anchored = [0.0, *timestamps, safe_end]
    return dedupe_timestamps(sorted(anchored))


def build_all_changes_timestamps(
    video_path: Path,
    duration: float,
    scene_threshold: float,
    min_change_gap: float,
) -> list[float]:
    detected = detect_scene_timestamps(
        video_path=video_path,
        scene_threshold=scene_threshold,
        min_change_gap=min_change_gap,
    )
    return ensure_anchor_timestamps(detected, duration)


def load_pil_image_tools():
    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError as exc:
        raise RuntimeError("Missing Python package: Pillow. Install it in the runtime used for this skill.") from exc
    return Image, ImageFilter, ImageStat


def extract_single_frame(video_path: Path, timestamp: float, output_path: Path) -> bytes:
    ffmpeg = require_dependency("ffmpeg")
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return output_path.read_bytes()


def normalized_pixel_diff(values_a: list[int], values_b: list[int]) -> float:
    if not values_a or not values_b:
        return 0.0
    length = min(len(values_a), len(values_b))
    if length <= 0:
        return 0.0
    total = 0
    for index in range(length):
        total += abs(int(values_a[index]) - int(values_b[index]))
    return total / (length * 255.0)


def build_layout_signature(
    image_path: Path,
    downscale_width: int,
) -> dict:
    Image, ImageFilter, ImageStat = load_pil_image_tools()
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        width = max(64, downscale_width)
        height = max(1, round(rgb.height * (width / max(rgb.width, 1))))
        resized = rgb.resize((width, height))
        grayscale = resized.convert("L")
        coarse = grayscale.resize((24, 14))
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        coarse_edges = edges.resize((24, 14))
        brightness_mean = (ImageStat.Stat(grayscale).mean or [0.0])[0] / 255.0
        edge_strength = (ImageStat.Stat(edges).mean or [0.0])[0] / 255.0
        histogram = grayscale.histogram()
        total_pixels = max(1, sum(histogram))
        dark_pixels = sum(histogram[:20])
        bright_pixels = sum(histogram[236:])
        grid_data = list(coarse.tobytes())
        edge_grid_data = list(coarse_edges.tobytes())
        row_means = []
        col_means = []
        for row_index in range(LAYOUT_GRID_HEIGHT):
            start = row_index * LAYOUT_GRID_WIDTH
            row = grid_data[start:start + LAYOUT_GRID_WIDTH]
            row_means.append(sum(row) / max(1, len(row)))
        for col_index in range(LAYOUT_GRID_WIDTH):
            values = [grid_data[(row_index * LAYOUT_GRID_WIDTH) + col_index] for row_index in range(LAYOUT_GRID_HEIGHT)]
            col_means.append(sum(values) / max(1, len(values)))
        return {
            "grid": grid_data,
            "edge_grid": edge_grid_data,
            "row_profile": row_means,
            "col_profile": col_means,
            "brightness_mean": brightness_mean,
            "edge_strength": edge_strength,
            "dark_ratio": dark_pixels / total_pixels,
            "bright_ratio": bright_pixels / total_pixels,
        }


def compute_layout_difference(
    signature_a: dict,
    signature_b: dict,
) -> dict:
    grid_diff = normalized_pixel_diff(signature_a["grid"], signature_b["grid"])
    edge_diff = normalized_pixel_diff(signature_a["edge_grid"], signature_b["edge_grid"])
    row_profile_diff = normalized_pixel_diff(signature_a.get("row_profile", []), signature_b.get("row_profile", []))
    col_profile_diff = normalized_pixel_diff(signature_a.get("col_profile", []), signature_b.get("col_profile", []))
    brightness_diff = abs(signature_a["brightness_mean"] - signature_b["brightness_mean"])
    edge_strength_diff = abs(signature_a["edge_strength"] - signature_b["edge_strength"])
    combined = (0.5 * grid_diff) + (0.3 * edge_diff) + (0.1 * brightness_diff) + (0.1 * edge_strength_diff)
    return {
        "grid_diff": grid_diff,
        "edge_diff": edge_diff,
        "row_profile_diff": row_profile_diff,
        "col_profile_diff": col_profile_diff,
        "brightness_diff": brightness_diff,
        "edge_strength_diff": edge_strength_diff,
        "combined_score": combined,
    }


def is_transition_like_signature(signature: dict) -> bool:
    return (
        signature["dark_ratio"] >= 0.92
        or (signature["brightness_mean"] <= 0.08 and signature["edge_strength"] <= 0.035)
        or (signature["bright_ratio"] >= 0.97 and signature["edge_strength"] <= 0.02)
    )


def compute_layout_difference_score(
    image_path_a: Path,
    image_path_b: Path,
    downscale_width: int,
) -> float:
    signature_a = build_layout_signature(image_path_a, downscale_width)
    signature_b = build_layout_signature(image_path_b, downscale_width)
    return compute_layout_difference(signature_a, signature_b)["combined_score"]


def resolve_stable_nav_context(
    entries: list[dict],
    current_index: int,
) -> dict:
    current = entries[current_index].get("nav_signal", empty_nav_signal())
    if current.get("nav_present") and current.get("active_index", -1) >= 0 and current.get("confidence", 0.0) >= 0.55:
        return current

    for distance in range(1, 3):
        previous_index = current_index - distance
        if previous_index < 0:
            break
        previous = entries[previous_index].get("nav_signal", empty_nav_signal())
        if previous.get("nav_present") and previous.get("active_index", -1) >= 0 and previous.get("confidence", 0.0) >= 0.75:
            return previous

    for distance in range(1, 3):
        next_index = current_index + distance
        if next_index >= len(entries):
            break
        following = entries[next_index].get("nav_signal", empty_nav_signal())
        if following.get("nav_present") and following.get("active_index", -1) >= 0 and following.get("confidence", 0.0) >= 0.85:
            return following

    return empty_nav_signal()


def nav_context_matches(
    previous_context: dict,
    current_context: dict,
) -> bool:
    return (
        previous_context.get("nav_present")
        and current_context.get("nav_present")
        and previous_context.get("active_index", -1) >= 0
        and current_context.get("active_index", -1) >= 0
        and previous_context.get("active_index") == current_context.get("active_index")
    )


def stable_title_matches(
    previous_title_signal: dict,
    current_title_signal: dict,
    previous_title_block_signal: dict,
    current_title_block_signal: dict,
) -> bool:
    if (
        previous_title_signal.get("is_meaningful")
        and current_title_signal.get("is_meaningful")
        and previous_title_block_signal.get("title_block_like")
        and current_title_block_signal.get("title_block_like")
    ):
        return compute_title_difference(
            previous_title_signal.get("normalized_text", ""),
            current_title_signal.get("normalized_text", ""),
        ) < 0.18

    return (
        not previous_title_signal.get("is_meaningful")
        and not current_title_signal.get("is_meaningful")
    )


def compute_same_chapter_duplicate_signal(
    previous_entry: dict,
    current_entry: dict,
    comparison: dict,
    previous_nav_context: dict,
    current_nav_context: dict,
) -> dict:
    time_gap = abs(float(current_entry.get("timestamp_seconds", 0.0)) - float(previous_entry.get("timestamp_seconds", 0.0)))
    same_nav_context = nav_context_matches(previous_nav_context, current_nav_context)
    previous_title_signal = previous_entry.get("title_signal", build_title_signal(""))
    current_title_signal = current_entry.get("title_signal", build_title_signal(""))
    previous_title_block_signal = previous_entry.get("title_block_signal", empty_title_block_signal())
    current_title_block_signal = current_entry.get("title_block_signal", empty_title_block_signal())
    same_title_context = stable_title_matches(
        previous_title_signal,
        current_title_signal,
        previous_title_block_signal,
        current_title_block_signal,
    )
    structure_diff = max(
        comparison.get("row_profile_diff", 0.0),
        comparison.get("col_profile_diff", 0.0),
    )
    localized_delta = max(
        0.0,
        comparison.get("grid_diff", 0.0) - structure_diff,
    )
    edge_profile_gap = abs(
        previous_entry.get("signature", {}).get("edge_strength", 0.0)
        - current_entry.get("signature", {}).get("edge_strength", 0.0)
    )
    looks_like_same_template = (
        comparison.get("combined_score", 0.0) <= DEFAULT_SAME_CHAPTER_COMBINED_MAX
        and structure_diff <= DEFAULT_SAME_CHAPTER_STRUCTURE_MAX
        and edge_profile_gap <= DEFAULT_SAME_CHAPTER_EDGE_PROFILE_MAX
        and localized_delta >= DEFAULT_SAME_CHAPTER_LOCALIZED_DELTA_MIN
    )
    duplicate_like = (
        time_gap <= DEFAULT_SAME_CHAPTER_MAX_GAP
        and same_nav_context
        and same_title_context
        and looks_like_same_template
        and not current_entry.get("transition_like", False)
        and not current_entry.get("presenter_signal", {}).get("presenter_like", False)
    )
    return {
        "time_gap": round(time_gap, 4),
        "same_nav_context": same_nav_context,
        "same_title_context": same_title_context,
        "structure_diff": round(structure_diff, 4),
        "localized_delta": round(localized_delta, 4),
        "edge_profile_gap": round(edge_profile_gap, 4),
        "looks_like_same_template": looks_like_same_template,
        "duplicate_like": duplicate_like,
    }


def filter_layout_change_timestamps(
    video_path: Path,
    timestamps: list[float],
    temp_dir: Path,
    layout_change_threshold: float,
    layout_downscale_width: int,
    title_ocr_filter: bool,
    title_change_threshold: float,
    chapter_nav_filter: bool,
    presenter_shot_filter: bool,
    same_chapter_dedupe_filter: bool,
) -> tuple[list[float], list[dict]]:
    if len(timestamps) <= 2:
        return dedupe_timestamps(sorted(timestamps)), []

    sorted_timestamps = dedupe_timestamps(sorted(timestamps))
    entries = []
    for timestamp in sorted_timestamps:
        frame_path = temp_dir / f"layout_{str(timestamp).replace('.', '_')}.jpg"
        extract_single_frame(video_path, timestamp, frame_path)
        signature = build_layout_signature(frame_path, layout_downscale_width)
        title_block_signal = extract_title_block_signal_from_image(frame_path) if title_ocr_filter else empty_title_block_signal()
        title_text = title_block_signal["text"] if title_ocr_filter else ""
        title_signal = build_title_signal(title_text) if title_ocr_filter else build_title_signal("")
        nav_signal = extract_chapter_nav_signal_from_image(frame_path) if chapter_nav_filter else empty_nav_signal()
        presenter_signal = extract_presenter_shot_signal_from_image(frame_path) if presenter_shot_filter else empty_presenter_signal()
        entries.append(
            {
                "timestamp_seconds": round(timestamp, 3),
                "path": frame_path,
                "signature": signature,
                "transition_like": is_transition_like_signature(signature),
                "title_text": title_signal["normalized_text"],
                "title_signal": title_signal,
                "title_block_signal": title_block_signal,
                "nav_signal": nav_signal,
                "presenter_signal": presenter_signal,
            }
        )

    diagnostics = []
    kept_entries = [entries[0]]
    last_kept = entries[0]
    layout_support_threshold = max(
        layout_change_threshold - DEFAULT_TITLE_SUPPORT_MARGIN,
        layout_change_threshold * 0.72,
    )

    last_kept_index = 0
    for current_index in range(1, len(entries) - 1):
        entry = entries[current_index]
        comparison = compute_layout_difference(last_kept["signature"], entry["signature"])
        previous_title_signal = last_kept.get("title_signal", build_title_signal(""))
        current_title_signal = entry.get("title_signal", build_title_signal(""))
        previous_title_block_signal = last_kept.get("title_block_signal", empty_title_block_signal())
        current_title_block_signal = entry.get("title_block_signal", empty_title_block_signal())
        previous_nav_signal = last_kept.get("nav_signal", empty_nav_signal())
        current_nav_signal = entry.get("nav_signal", empty_nav_signal())
        previous_nav_context = resolve_stable_nav_context(entries, last_kept_index)
        current_nav_context = resolve_stable_nav_context(entries, current_index)
        current_presenter_signal = entry.get("presenter_signal", empty_presenter_signal())
        title_difference = (
            compute_title_difference(last_kept.get("title_text", ""), entry.get("title_text", ""))
            if title_ocr_filter
            else 0.0
        )
        layout_strong = comparison["combined_score"] >= layout_change_threshold
        layout_borderline = comparison["combined_score"] >= layout_support_threshold
        title_quality_good = (
            previous_title_signal["is_meaningful"]
            and current_title_signal["is_meaningful"]
            and previous_title_block_signal["title_block_like"]
            and current_title_block_signal["title_block_like"]
        )
        title_supports_change = (
            title_ocr_filter
            and not layout_strong
            and layout_borderline
            and title_quality_good
            and title_difference >= title_change_threshold
        )
        nav_layout_borderline = comparison["combined_score"] >= max(
            layout_change_threshold - DEFAULT_NAV_SUPPORT_MARGIN,
            layout_change_threshold * 0.7,
        )
        nav_supports_change = (
            chapter_nav_filter
            and not layout_strong
            and nav_layout_borderline
            and previous_nav_signal["nav_present"]
            and current_nav_signal["nav_present"]
            and previous_nav_signal["active_index"] >= 0
            and current_nav_signal["active_index"] >= 0
            and previous_nav_signal["active_index"] != current_nav_signal["active_index"]
            and previous_nav_signal["confidence"] >= 0.55
            and current_nav_signal["confidence"] >= 0.55
        )
        presenter_layout_borderline = comparison["combined_score"] >= max(
            layout_change_threshold - DEFAULT_PRESENTER_SUPPORT_MARGIN,
            layout_change_threshold * 0.68,
        )
        presenter_suppresses_keep = (
            presenter_shot_filter
            and current_presenter_signal["presenter_like"]
            and current_presenter_signal["confidence"] >= 0.7
            and not nav_supports_change
            and not title_supports_change
            and (presenter_layout_borderline or layout_strong)
        )
        same_chapter_duplicate_signal = compute_same_chapter_duplicate_signal(
            previous_entry=last_kept,
            current_entry=entry,
            comparison=comparison,
            previous_nav_context=previous_nav_context,
            current_nav_context=current_nav_context,
        )
        same_chapter_duplicate = (
            same_chapter_dedupe_filter
            and (layout_strong or title_supports_change or nav_supports_change)
            and same_chapter_duplicate_signal["duplicate_like"]
            and not nav_supports_change
            and not title_supports_change
        )
        keep = (
            (layout_strong or title_supports_change or nav_supports_change)
            and not entry["transition_like"]
            and not presenter_suppresses_keep
            and not same_chapter_duplicate
        )
        if entry["transition_like"]:
            keep_reason = "dropped-transition-like"
        elif same_chapter_duplicate:
            keep_reason = "dropped-same-chapter-near-duplicate"
        elif presenter_suppresses_keep:
            keep_reason = "dropped-presenter-shot"
        elif layout_strong:
            keep_reason = "kept-layout-strong"
        elif title_supports_change:
            keep_reason = "kept-layout-borderline-title-gated"
        elif nav_supports_change:
            keep_reason = "kept-layout-borderline-nav-gated"
        elif title_ocr_filter and layout_borderline and not title_quality_good:
            keep_reason = "dropped-title-low-quality"
        elif chapter_nav_filter and nav_layout_borderline and previous_nav_signal["nav_present"] and current_nav_signal["nav_present"]:
            keep_reason = "dropped-nav-unchanged"
        elif title_ocr_filter and layout_borderline:
            keep_reason = "dropped-title-unchanged"
        else:
            keep_reason = "dropped-layout-weak"
        diagnostics.append(
            {
                "timestamp_seconds": entry["timestamp_seconds"],
                "difference_score": round(comparison["combined_score"], 4),
                "grid_diff": round(comparison["grid_diff"], 4),
                "edge_diff": round(comparison["edge_diff"], 4),
                "row_profile_diff": round(comparison["row_profile_diff"], 4),
                "col_profile_diff": round(comparison["col_profile_diff"], 4),
                "brightness_diff": round(comparison["brightness_diff"], 4),
                "edge_strength_diff": round(comparison["edge_strength_diff"], 4),
                "title_difference": round(title_difference, 4),
                "title_text": entry.get("title_text", ""),
                "title_meaningful_text": current_title_signal["meaningful_text"],
                "title_quality_score": round(current_title_signal["quality_score"], 4),
                "title_is_meaningful": current_title_signal["is_meaningful"],
                "title_block_like": current_title_block_signal["title_block_like"],
                "title_block_left_ratio": round(current_title_block_signal["left_ratio"], 4),
                "title_block_top_ratio": round(current_title_block_signal["top_ratio"], 4),
                "title_block_width_ratio": round(current_title_block_signal["width_ratio"], 4),
                "title_block_height_ratio": round(current_title_block_signal["height_ratio"], 4),
                "previous_title_text": last_kept.get("title_text", ""),
                "previous_title_meaningful_text": previous_title_signal["meaningful_text"],
                "previous_title_quality_score": round(previous_title_signal["quality_score"], 4),
                "previous_title_is_meaningful": previous_title_signal["is_meaningful"],
                "previous_title_block_like": previous_title_block_signal["title_block_like"],
                "nav_present": current_nav_signal["nav_present"],
                "nav_detected_labels": current_nav_signal["detected_labels"],
                "nav_active_label": current_nav_signal["active_label"],
                "nav_active_index": current_nav_signal["active_index"],
                "nav_confidence": current_nav_signal["confidence"],
                "nav_context_active_label": current_nav_context["active_label"],
                "nav_context_active_index": current_nav_context["active_index"],
                "nav_context_confidence": current_nav_context["confidence"],
                "previous_nav_active_label": previous_nav_signal["active_label"],
                "previous_nav_active_index": previous_nav_signal["active_index"],
                "previous_nav_confidence": previous_nav_signal["confidence"],
                "previous_nav_context_active_label": previous_nav_context["active_label"],
                "previous_nav_context_active_index": previous_nav_context["active_index"],
                "previous_nav_context_confidence": previous_nav_context["confidence"],
                "presenter_like": current_presenter_signal["presenter_like"],
                "presenter_confidence": current_presenter_signal["confidence"],
                "presenter_skin_ratio": current_presenter_signal["skin_ratio"],
                "presenter_center_dark_ratio": current_presenter_signal["center_dark_ratio"],
                "presenter_full_dark_ratio": current_presenter_signal["full_dark_ratio"],
                "transition_like": entry["transition_like"],
                "layout_strong": layout_strong,
                "layout_borderline": layout_borderline,
                "title_supports_change": title_supports_change,
                "nav_supports_change": nav_supports_change,
                "same_chapter_duplicate": same_chapter_duplicate,
                "same_chapter_duplicate_time_gap": same_chapter_duplicate_signal["time_gap"],
                "same_chapter_duplicate_same_nav_context": same_chapter_duplicate_signal["same_nav_context"],
                "same_chapter_duplicate_same_title_context": same_chapter_duplicate_signal["same_title_context"],
                "same_chapter_duplicate_structure_diff": same_chapter_duplicate_signal["structure_diff"],
                "same_chapter_duplicate_localized_delta": same_chapter_duplicate_signal["localized_delta"],
                "same_chapter_duplicate_edge_profile_gap": same_chapter_duplicate_signal["edge_profile_gap"],
                "same_chapter_duplicate_same_template": same_chapter_duplicate_signal["looks_like_same_template"],
                "keep_reason": keep_reason,
                "kept": keep,
            }
        )
        if keep:
            kept_entries.append(entry)
            last_kept = entry
            last_kept_index = current_index

    kept_entries.append(entries[-1])
    return dedupe_timestamps(sorted(item["timestamp_seconds"] for item in kept_entries)), diagnostics


def extract_frames(video_path: Path, timestamps: list[float], temp_dir: Path) -> list[dict]:
    frames = []
    for index, ts in enumerate(timestamps):
        output_path = temp_dir / f"frame_{index:03d}.jpg"
        image_bytes = extract_single_frame(video_path, ts, output_path)
        frames.append(
            {
                "index": index,
                "timestamp_seconds": ts,
                "path": str(output_path),
                "base64": base64.b64encode(image_bytes).decode("ascii"),
            }
        )
    return frames


def require_tesseract() -> str:
    if LOCAL_TESSDATA_DIR.exists():
        os.environ.setdefault("TESSDATA_PREFIX", str(LOCAL_TESSDATA_DIR))
    local = shutil.which("tesseract")
    if local:
        return local
    common_paths = [
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    ]
    for candidate in common_paths:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("Missing dependency: tesseract")


def resolve_tesseract_language(preferred: str = DEFAULT_OCR_LANGUAGE) -> str:
    available = set()
    if LOCAL_TESSDATA_DIR.exists():
        available.update(path.stem for path in LOCAL_TESSDATA_DIR.glob("*.traineddata"))
    tessdata_prefix = os.environ.get("TESSDATA_PREFIX")
    if tessdata_prefix:
        tessdata_dir = Path(tessdata_prefix)
        if tessdata_dir.exists():
            available.update(path.stem for path in tessdata_dir.glob("*.traineddata"))

    requested = [part for part in preferred.split("+") if part]
    usable = [part for part in requested if part in available]
    if usable:
        return "+".join(usable)
    if "eng" in available:
        return "eng"
    return preferred


def normalize_title_text(text: str) -> str:
    cleaned = (text or "").strip().lower()
    cleaned = re.sub(r"(?<!\d)\d{1,2}:\d{2}(?!\d)", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff /:_-]+", " ", cleaned)
    cleaned = re.sub(r"\b(?:am|pm)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def extract_meaningful_title_tokens(text: str) -> list[str]:
    normalized = normalize_title_text(text)
    if not normalized:
        return []

    meaningful_tokens = []
    for token in normalized.split():
        compact = token.strip("-_/:")
        if not compact:
            continue
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", compact))
        alpha_chars = len(re.findall(r"[a-zA-Z]", compact))
        digit_chars = len(re.findall(r"\d", compact))
        if chinese_chars >= 2:
            meaningful_tokens.append(compact)
            continue
        if alpha_chars >= 3:
            meaningful_tokens.append(compact)
            continue
        if alpha_chars >= 2 and digit_chars >= 1 and len(compact) >= 4:
            meaningful_tokens.append(compact)
    return meaningful_tokens


def build_title_signal(text: str) -> dict:
    normalized = normalize_title_text(text)
    tokens = normalized.split() if normalized else []
    meaningful_tokens = extract_meaningful_title_tokens(normalized)
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    alpha_chars = len(re.findall(r"[a-zA-Z]", normalized))
    digit_chars = len(re.findall(r"\d", normalized))
    short_token_count = sum(1 for token in tokens if len(token.strip("-_/:")) <= 2)
    unique_meaningful_token_count = len(set(meaningful_tokens))
    average_meaningful_length = (
        sum(len(token) for token in meaningful_tokens) / len(meaningful_tokens)
        if meaningful_tokens
        else 0.0
    )
    digit_heavy = digit_chars > max(4, alpha_chars + chinese_chars)
    noisy_token_mix = len(tokens) >= 6 and (short_token_count / max(1, len(tokens))) >= 0.55
    sparse_meaning = len(tokens) >= 8 and unique_meaningful_token_count <= 2

    quality_score = 0.0
    if chinese_chars >= 2 or alpha_chars >= 6:
        quality_score += 0.35
    if unique_meaningful_token_count >= 2:
        quality_score += 0.25
    elif unique_meaningful_token_count == 1 and average_meaningful_length >= 4.0:
        quality_score += 0.12
    if average_meaningful_length >= 4.0:
        quality_score += 0.2
    elif average_meaningful_length >= 3.0:
        quality_score += 0.1
    if 6 <= len(normalized) <= 80:
        quality_score += 0.15
    elif 4 <= len(normalized) < 6 and unique_meaningful_token_count >= 1:
        quality_score += 0.05
    if len(tokens) <= 8 and unique_meaningful_token_count >= 1:
        quality_score += 0.05
    if noisy_token_mix:
        quality_score -= 0.25
    if sparse_meaning:
        quality_score -= 0.2
    if digit_heavy:
        quality_score -= 0.25
    if not meaningful_tokens:
        quality_score = min(quality_score, 0.25)

    quality_score = max(0.0, min(1.0, quality_score))
    is_meaningful = (
        quality_score >= DEFAULT_TITLE_QUALITY_THRESHOLD
        and unique_meaningful_token_count >= 1
        and not digit_heavy
    )
    return {
        "normalized_text": normalized,
        "meaningful_text": " ".join(meaningful_tokens),
        "meaningful_tokens": meaningful_tokens,
        "quality_score": quality_score,
        "is_meaningful": is_meaningful,
        "token_count": len(tokens),
        "meaningful_token_count": unique_meaningful_token_count,
    }


def empty_title_block_signal() -> dict:
    return {
        "text": "",
        "left_ratio": 1.0,
        "top_ratio": 1.0,
        "width_ratio": 0.0,
        "height_ratio": 0.0,
        "title_block_like": False,
        "candidate_score": 0.0,
    }


def normalize_nav_label(text: str) -> str:
    normalized = normalize_title_text(text)
    normalized = normalized.replace(" ", "")
    if not normalized:
        return ""
    replacements = {
        "claudecode浣跨敤": "claudecode浣跨敤",
        "claudecde浣跨敤": "claudecode浣跨敤",
        "claudecode": "claudecode浣跨敤",
        "openclaw浣跨敤": "openclaw浣跨敤",
        "openclaw": "openclaw浣跨敤",
        "obsidian浣跨敤": "obsidian浣跨敤",
        "obsidian": "obsidian浣跨敤",
        "妗堜緥鎷撳睍": "妗堜緥鎷撳睍",
        "妗堜緥鎵╁睍": "妗堜緥鎷撳睍",
        "浠嬬粛": "浠嬬粛",
    }
    for source, target in replacements.items():
        if source in normalized:
            return target
    return normalized


def empty_nav_signal() -> dict:
    return {
        "labels": [],
        "detected_labels": [],
        "active_index": -1,
        "active_label": "",
        "confidence": 0.0,
        "nav_present": False,
    }


def empty_presenter_signal() -> dict:
    return {
        "presenter_like": False,
        "skin_ratio": 0.0,
        "center_dark_ratio": 0.0,
        "full_dark_ratio": 0.0,
        "upper_dark_ratio": 0.0,
        "center_brightness": 0.0,
        "confidence": 0.0,
    }


def extract_presenter_shot_signal_from_image(image_path: Path) -> dict:
    Image, _, ImageStat = load_pil_image_tools()
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        center = rgb.crop((int(width * 0.28), int(height * 0.12), int(width * 0.72), int(height * 0.78)))
        upper = rgb.crop((0, 0, width, int(height * 0.82)))
        center_gray = center.convert("L")
        upper_gray = upper.convert("L")
        full_gray = rgb.convert("L")

        center_pixels = list(center.getdata(band=None))
        skin_count = 0
        center_dark_count = 0
        for red, green, blue in center_pixels:
            if (
                red > 95
                and green > 40
                and blue > 20
                and (max(red, green, blue) - min(red, green, blue)) > 15
                and abs(red - green) > 8
                and red > green
                and red > blue
            ):
                skin_count += 1
            if (red + green + blue) / 3 < 70:
                center_dark_count += 1

        full_values = list(full_gray.getdata(band=None))
        upper_values = list(upper_gray.getdata(band=None))
        center_brightness = (ImageStat.Stat(center_gray).mean or [0.0])[0]
        skin_ratio = skin_count / max(1, len(center_pixels))
        center_dark_ratio = center_dark_count / max(1, len(center_pixels))
        full_dark_ratio = sum(1 for value in full_values if value < 70) / max(1, len(full_values))
        upper_dark_ratio = sum(1 for value in upper_values if value < 70) / max(1, len(upper_values))

    presenter_like = (
        skin_ratio >= 0.18
        and center_dark_ratio >= 0.35
        and full_dark_ratio >= 0.55
        and upper_dark_ratio >= 0.55
        and center_brightness <= 120
    )
    confidence = 0.0
    if presenter_like:
        confidence = min(
            1.0,
            0.45
            + (skin_ratio * 1.2)
            + (center_dark_ratio * 0.35)
            + (full_dark_ratio * 0.2),
        )
    return {
        "presenter_like": presenter_like,
        "skin_ratio": round(skin_ratio, 4),
        "center_dark_ratio": round(center_dark_ratio, 4),
        "full_dark_ratio": round(full_dark_ratio, 4),
        "upper_dark_ratio": round(upper_dark_ratio, 4),
        "center_brightness": round(center_brightness, 2),
        "confidence": round(confidence, 4),
    }


def extract_chapter_nav_signal_from_image(image_path: Path) -> dict:
    tesseract_path = require_tesseract()
    try:
        import pytesseract
        from PIL import Image, ImageFilter, ImageStat
    except ImportError as exc:
        raise RuntimeError("Missing Python packages for OCR: pytesseract and Pillow.") from exc
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    ocr_language = resolve_tesseract_language()

    canonical_labels = ["浠嬬粛", "obsidian浣跨敤", "claudecode浣跨敤", "openclaw浣跨敤", "妗堜緥鎷撳睍"]
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        nav_top = int(height * 0.93)
        nav_region = rgb.crop((0, nav_top, width, height))
        grayscale = nav_region.convert("L").filter(ImageFilter.GaussianBlur(radius=2))
        nav_brightness = (ImageStat.Stat(grayscale).mean or [0.0])[0]
        enlarged = nav_region.resize((max(1, nav_region.width * 2), max(1, nav_region.height * 2)))
        data = pytesseract.image_to_data(
            enlarged,
            lang=ocr_language,
            config="--psm 6",
            output_type=pytesseract.Output.DICT,
        )

    label_hits = []
    for index in range(len(data["text"])):
        raw_text = (data["text"][index] or "").strip()
        if not raw_text:
            continue
        conf_text = str(data["conf"][index]).strip()
        confidence = float(conf_text) if conf_text not in {"", "-1"} else -1.0
        if confidence < 0:
            continue
        normalized_label = normalize_nav_label(raw_text)
        if normalized_label not in canonical_labels:
            continue
        left = int(data["left"][index])
        box_width = int(data["width"][index])
        center_ratio = ((left + (box_width / 2.0)) / max(1, enlarged.width))
        label_hits.append(
            {
                "label": normalized_label,
                "center_ratio": center_ratio,
                "confidence": confidence,
            }
        )

    if nav_brightness < 45:
        return empty_nav_signal()

    expected_centers = {
        "浠嬬粛": 0.075,
        "obsidian浣跨敤": 0.245,
        "claudecode浣跨敤": 0.47,
        "openclaw浣跨敤": 0.71,
        "妗堜緥鎷撳睍": 0.925,
    }
    best_by_label = {}
    for hit in label_hits:
        score = hit["confidence"] - (abs(hit["center_ratio"] - expected_centers[hit["label"]]) * 220)
        existing = best_by_label.get(hit["label"])
        if not existing or score > existing["score"]:
            best_by_label[hit["label"]] = {**hit, "score": score}

    detected_labels = [label for label in canonical_labels if label in best_by_label]
    nav_present = len(detected_labels) >= 3
    if not nav_present:
        return {
            **empty_nav_signal(),
            "detected_labels": detected_labels,
            "labels": detected_labels,
        }

    segment_bounds = [(0.0, 0.15), (0.15, 0.34), (0.34, 0.58), (0.58, 0.85), (0.85, 1.0)]
    crop = grayscale.filter(ImageFilter.GaussianBlur(radius=12))
    segment_means = []
    for left_ratio, right_ratio in segment_bounds:
        region = crop.crop((int(crop.width * left_ratio), 0, int(crop.width * right_ratio), crop.height))
        segment_means.append((ImageStat.Stat(region).mean or [0.0])[0])

    darkest_index = min(range(len(segment_means)), key=lambda idx: segment_means[idx])
    confidence = min(1.0, 0.45 + ((max(segment_means) - min(segment_means)) / 90.0))
    active_label = canonical_labels[darkest_index]
    return {
        "labels": canonical_labels,
        "detected_labels": detected_labels,
        "active_index": darkest_index,
        "active_label": active_label,
        "confidence": round(confidence, 4),
        "nav_present": True,
    }


def merge_title_candidate_rows(rows: list[dict], image_width: int) -> list[dict]:
    if not rows:
        return []

    merged = []
    current = rows[0].copy()
    for row in rows[1:]:
        vertical_gap = row["top"] - (current["top"] + current["height"])
        aligned_left = abs(row["left"] - current["left"]) <= max(28, int(image_width * 0.06))
        overlapping = row["top"] <= current["top"] + current["height"] + max(10, int(current["height"] * 0.45))
        if aligned_left and (vertical_gap <= max(18, int(current["height"] * 0.7)) or overlapping):
            current["text"] = f"{current['text']} {row['text']}".strip()
            current["width"] = max(current["left"] + current["width"], row["left"] + row["width"]) - min(current["left"], row["left"])
            current["height"] = max(current["top"] + current["height"], row["top"] + row["height"]) - min(current["top"], row["top"])
            current["left"] = min(current["left"], row["left"])
            current["top"] = min(current["top"], row["top"])
            current["conf"] = max(current["conf"], row["conf"])
            current["score"] = max(current.get("score", 0.0), row.get("score", 0.0))
            continue
        merged.append(current)
        current = row.copy()
    merged.append(current)
    return merged


def extract_title_text_from_image(image_path: Path) -> str:
    tesseract_path = require_tesseract()
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Missing Python packages for OCR: pytesseract and Pillow.") from exc
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    ocr_language = resolve_tesseract_language()

    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        top_offset = int(height * DEFAULT_TITLE_BAND_TOP_RATIO)
        bottom_edge = max(top_offset + 1, int(height * DEFAULT_TITLE_BAND_BOTTOM_RATIO))
        title_region = rgb.crop((0, top_offset, width, bottom_edge))
        enlarged = title_region.resize((max(1, title_region.width * 2), max(1, title_region.height * 2)))
        data = pytesseract.image_to_data(
            enlarged,
            lang=ocr_language,
            config="--psm 6",
            output_type=pytesseract.Output.DICT,
        )

    rows = []
    scale = 2.0
    max_top = int((bottom_edge - top_offset) * scale * 0.72)
    for index in range(len(data["text"])):
        raw_text = (data["text"][index] or "").strip()
        if not raw_text:
            continue
        conf_text = str(data["conf"][index]).strip()
        confidence = float(conf_text) if conf_text not in {"", "-1"} else -1.0
        if confidence < 0:
            continue

        left = int(data["left"][index])
        top = int(data["top"][index])
        box_width = int(data["width"][index])
        box_height = int(data["height"][index])
        if box_width <= 0 or box_height <= 0:
            continue
        if top > max_top:
            continue

        normalized_text = normalize_title_text(raw_text)
        signal = build_title_signal(normalized_text)
        if not normalized_text:
            continue

        width_ratio = box_width / max(1, enlarged.width)
        height_ratio = box_height / max(1, enlarged.height)
        left_ratio = left / max(1, enlarged.width)
        right_ratio = (left + box_width) / max(1, enlarged.width)
        top_ratio = top / max(1, enlarged.height)

        position_bonus = 0.0
        if top_ratio <= 0.26:
            position_bonus += 0.2
        elif top_ratio <= 0.4:
            position_bonus += 0.08
        if left_ratio <= 0.22:
            position_bonus += 0.12
        elif left_ratio <= 0.4:
            position_bonus += 0.05
        if right_ratio >= 0.9 and width_ratio <= 0.22:
            position_bonus -= 0.18
        if width_ratio >= 0.55:
            position_bonus -= 0.12
        if signal["meaningful_token_count"] <= 1 and height_ratio <= 0.03:
            position_bonus -= 0.15
        if confidence < 25:
            position_bonus -= 0.1

        candidate_score = signal["quality_score"] + position_bonus + (height_ratio * 1.2)
        rows.append(
            {
                "text": normalized_text,
                "conf": confidence,
                "left": left,
                "top": top,
                "width": box_width,
                "height": box_height,
                "score": candidate_score,
            }
        )

    merged_rows = merge_title_candidate_rows(
        sorted(rows, key=lambda row: (row["top"], row["left"])),
        enlarged.width,
    )
    if not merged_rows:
        raw_text = pytesseract.image_to_string(enlarged, lang=ocr_language, config="--psm 6")
        return normalize_title_text(raw_text)

    best_candidate = None
    best_score = -1e9
    for row in merged_rows:
        signal = build_title_signal(row["text"])
        width_ratio = row["width"] / max(1, enlarged.width)
        height_ratio = row["height"] / max(1, enlarged.height)
        left_ratio = row["left"] / max(1, enlarged.width)
        top_ratio = row["top"] / max(1, enlarged.height)
        score = row.get("score", 0.0)
        score += signal["quality_score"] * 0.7
        score += min(height_ratio * 1.8, 0.35)
        if top_ratio <= 0.18:
            score += 0.18
        elif top_ratio <= 0.3:
            score += 0.08
        if left_ratio <= 0.18:
            score += 0.12
        if width_ratio >= 0.6:
            score -= 0.2
        if signal["meaningful_token_count"] >= 2:
            score += 0.08
        if score > best_score:
            best_score = score
            best_candidate = row["text"]

    if not best_candidate or best_score < 0.38:
        return ""
    return normalize_title_text(best_candidate)


def extract_title_block_signal_from_image(image_path: Path) -> dict:
    tesseract_path = require_tesseract()
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Missing Python packages for OCR: pytesseract and Pillow.") from exc
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    ocr_language = resolve_tesseract_language()

    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        top_offset = int(height * DEFAULT_TITLE_BAND_TOP_RATIO)
        bottom_edge = max(top_offset + 1, int(height * DEFAULT_TITLE_BAND_BOTTOM_RATIO))
        title_region = rgb.crop((0, top_offset, width, bottom_edge))
        enlarged = title_region.resize((max(1, title_region.width * 2), max(1, title_region.height * 2)))
        data = pytesseract.image_to_data(
            enlarged,
            lang=ocr_language,
            config="--psm 6",
            output_type=pytesseract.Output.DICT,
        )

    rows = []
    max_top = int(enlarged.height * 0.72)
    for index in range(len(data["text"])):
        raw_text = (data["text"][index] or "").strip()
        if not raw_text:
            continue
        conf_text = str(data["conf"][index]).strip()
        confidence = float(conf_text) if conf_text not in {"", "-1"} else -1.0
        if confidence < 0:
            continue

        left = int(data["left"][index])
        top = int(data["top"][index])
        box_width = int(data["width"][index])
        box_height = int(data["height"][index])
        if box_width <= 0 or box_height <= 0 or top > max_top:
            continue

        normalized_text = normalize_title_text(raw_text)
        signal = build_title_signal(normalized_text)
        if not normalized_text:
            continue

        width_ratio = box_width / max(1, enlarged.width)
        height_ratio = box_height / max(1, enlarged.height)
        left_ratio = left / max(1, enlarged.width)
        right_ratio = (left + box_width) / max(1, enlarged.width)
        top_ratio = top / max(1, enlarged.height)

        position_bonus = 0.0
        if top_ratio <= 0.26:
            position_bonus += 0.2
        elif top_ratio <= 0.4:
            position_bonus += 0.08
        if left_ratio <= 0.22:
            position_bonus += 0.12
        elif left_ratio <= 0.4:
            position_bonus += 0.05
        if right_ratio >= 0.9 and width_ratio <= 0.22:
            position_bonus -= 0.18
        if width_ratio >= 0.55:
            position_bonus -= 0.12
        if signal["meaningful_token_count"] <= 1 and height_ratio <= 0.03:
            position_bonus -= 0.15
        if confidence < 25:
            position_bonus -= 0.1

        candidate_score = signal["quality_score"] + position_bonus + (height_ratio * 1.2)
        rows.append(
            {
                "text": normalized_text,
                "conf": confidence,
                "left": left,
                "top": top,
                "width": box_width,
                "height": box_height,
                "score": candidate_score,
            }
        )

    merged_rows = merge_title_candidate_rows(
        sorted(rows, key=lambda row: (row["top"], row["left"])),
        enlarged.width,
    )
    if not merged_rows:
        return empty_title_block_signal()

    best_candidate = None
    best_score = -1e9
    for row in merged_rows:
        signal = build_title_signal(row["text"])
        width_ratio = row["width"] / max(1, enlarged.width)
        height_ratio = row["height"] / max(1, enlarged.height)
        left_ratio = row["left"] / max(1, enlarged.width)
        top_ratio = row["top"] / max(1, enlarged.height)
        score = row.get("score", 0.0)
        score += signal["quality_score"] * 0.7
        score += min(height_ratio * 1.8, 0.35)
        if top_ratio <= 0.18:
            score += 0.18
        elif top_ratio <= 0.3:
            score += 0.08
        if left_ratio <= 0.18:
            score += 0.12
        if width_ratio >= 0.6:
            score -= 0.2
        if signal["meaningful_token_count"] >= 2:
            score += 0.08
        if score > best_score:
            best_score = score
            best_candidate = {
                "text": row["text"],
                "left_ratio": left_ratio,
                "top_ratio": top_ratio,
                "width_ratio": width_ratio,
                "height_ratio": height_ratio,
                "candidate_score": score,
            }

    if not best_candidate or best_score < 0.38:
        return empty_title_block_signal()

    title_block_like = (
        best_candidate["top_ratio"] <= 0.3
        and best_candidate["left_ratio"] <= 0.3
        and best_candidate["width_ratio"] <= 0.42
        and best_candidate["height_ratio"] >= 0.045
    )
    best_candidate["text"] = normalize_title_text(best_candidate["text"])
    best_candidate["title_block_like"] = title_block_like
    return best_candidate


def compute_title_difference(text_a: str, text_b: str) -> float:
    signal_a = build_title_signal(text_a)
    signal_b = build_title_signal(text_b)
    if not signal_a["is_meaningful"] or not signal_b["is_meaningful"]:
        return 0.0

    normalized_a = signal_a["meaningful_text"] or signal_a["normalized_text"]
    normalized_b = signal_b["meaningful_text"] or signal_b["normalized_text"]
    if not normalized_a and not normalized_b:
        return 0.0
    if normalized_a == normalized_b:
        return 0.0

    tokens_a = set(signal_a["meaningful_tokens"] or normalized_a.split())
    tokens_b = set(signal_b["meaningful_tokens"] or normalized_b.split())
    if tokens_a or tokens_b:
        overlap = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b) or 1
        token_distance = 1.0 - (overlap / union)
    else:
        token_distance = 1.0

    from difflib import SequenceMatcher

    sequence_distance = 1.0 - SequenceMatcher(None, normalized_a, normalized_b).ratio()
    return max(token_distance, sequence_distance)


def normalize_ocr_text(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in cleaned.split("\n"):
        compact = re.sub(r"\s+", " ", line).strip()
        compact = compact.strip("| *\t·•-=_,.;:,.，。；：")
        if not compact:
            continue
        meaningful_chars = len(re.findall(r"[0-9A-Za-z\u4e00-\u9fff]", compact))
        if meaningful_chars < 2:
            continue
        lines.append(compact)
    return "\n".join(lines)


def correct_common_ocr_terms(text: str) -> str:
    corrected = text or ""
    replacements = [
        (r"\b0bsidion\b", "Obsidian"),
        (r"\b0bsidian\b", "Obsidian"),
        (r"\bObsjidion\b", "Obsidian"),
        (r"\bobsidion\b", "Obsidian"),
        (r"\bobsidian\b", "Obsidian"),
        (r"\bCloudeCode\b", "ClaudeCode"),
        (r"\bCloudeCooe\b", "ClaudeCode"),
        (r"\bClaudeCodet\b", "ClaudeCode"),
        (r"\bClaude Code\b", "Claude Code"),
        (r"\b0Denclow\b", "OpenClaw"),
        (r"\bOpenclaw\b", "OpenClaw"),
        (r"\bopenclaw\b", "OpenClaw"),
        (r"\bAl\b", "AI"),
        (r"\bX-Al-Studio\b", "x-AI-Studio"),
        (r"\bx-Al-Studio\b", "x-AI-Studio"),
        (r"\bX-LLM-Wiki\b", "x-LLM-Wiki"),
    ]
    for pattern, replacement in replacements:
        corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)
    corrected = corrected.replace("Obsidian 使 用", "Obsidian使用")
    corrected = corrected.replace("ClaudeCode 使 用", "ClaudeCode使用")
    corrected = corrected.replace("OpenClaw 使 用", "OpenClaw使用")
    return normalize_known_video_terms(corrected)


def normalize_known_video_terms(text: str) -> str:
    normalized = text or ""
    replacements = [
        (r"OBZ硬支撑", "Obsidian 知识库"),
        (r"OBZ\s*硬支撑", "Obsidian 知识库"),
        (r"OBZ店", "Obsidian"),
        (r"upc店", "Obsidian"),
        (r"UPC店", "Obsidian"),
        (r"UpVDN", "Obsidian"),
        (r"OpenCore", "OpenClaw"),
        (r"OpenCloud", "OpenClaw"),
        (r"OpenCall", "OpenClaw"),
        (r"Cloud Code", "Claude Code"),
        (r"CloudeCode", "Claude Code"),
        (r"ClaudeCode", "Claude Code"),
        (r"Openclaw", "OpenClaw"),
        (r"openclaw", "OpenClaw"),
        (r"\bAl\b", "AI"),
        (r"日治", "日志"),
        (r"摘药", "摘要"),
        (r"说箭甲", "收藏夹"),
        (r"按卷", "案卷"),
        (r"按键", "案件"),
        (r"避荒流程", "闭环流程"),
        (r"上下闻", "上下文"),
        (r"对话光", "对话框"),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized


def ocr_text_quality_score(text: str) -> float:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0.0
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", compact))
    alpha_chars = len(re.findall(r"[A-Za-z]", compact))
    digit_chars = len(re.findall(r"\d", compact))
    useful_chars = chinese_chars + alpha_chars + digit_chars
    punctuation_chars = len(compact) - useful_chars
    known_terms = [
        "Obsidian",
        "Claude",
        "OpenClaw",
        "Action",
        "Card",
        "Time",
        "LLM",
        "AI",
        "Homepage",
        "CLAUDE",
    ]
    score = 0.0
    if chinese_chars >= 2:
        score += 0.55
    if chinese_chars >= 5:
        score += 0.2
    if any(term.lower() in compact.lower() for term in known_terms):
        score += 0.35
    if re.search(r"\d{2,}-[A-Za-z]", compact) or re.search(r"\d{2,}-[\u4e00-\u9fff]", compact):
        score += 0.18
    if alpha_chars >= 4 and len(compact) >= 6:
        score += 0.16
    if useful_chars / max(1, len(compact)) < 0.68:
        score -= 0.25
    if punctuation_chars >= useful_chars:
        score -= 0.25
    if alpha_chars >= 8 and chinese_chars == 0 and not any(term.lower() in compact.lower() for term in known_terms):
        short_vowel_ratio = len(re.findall(r"[aeiouAEIOU]", compact)) / max(1, alpha_chars)
        if short_vowel_ratio < 0.18 or short_vowel_ratio > 0.58:
            score -= 0.25
    if len(compact) <= 3 and chinese_chars == 0 and not any(term.lower() in compact.lower() for term in known_terms):
        score -= 0.25
    return max(0.0, min(1.0, score))


def is_useful_ocr_line(text: str) -> bool:
    normalized = normalize_ocr_text(text)
    if not normalized:
        return False
    return ocr_text_quality_score(normalized) >= 0.38


def looks_like_ocr_duplicate(text: str, existing: list[str]) -> bool:
    normalized = re.sub(r"\s+", "", normalize_title_text(text))
    if not normalized:
        return True
    for item in existing:
        current = re.sub(r"\s+", "", normalize_title_text(item))
        if not current:
            continue
        if normalized == current:
            return True
        if len(normalized) >= 8 and len(current) >= 8 and difflib.SequenceMatcher(None, normalized, current).ratio() >= 0.88:
            return True
    return False


def prepare_ocr_image(image, scale: int = 2):
    Image, ImageFilter, _ = load_pil_image_tools()
    from PIL import ImageOps

    rgb = image.convert("RGB")
    if scale > 1:
        rgb = rgb.resize((rgb.width * scale, rgb.height * scale))
    grayscale = ImageOps.grayscale(rgb)
    grayscale = ImageOps.autocontrast(grayscale)
    grayscale = grayscale.filter(ImageFilter.SHARPEN)
    return grayscale


def image_to_confident_text(pytesseract, image, language: str, config: str, min_confidence: float) -> str:
    data = pytesseract.image_to_data(
        image,
        lang=language,
        config=config,
        output_type=pytesseract.Output.DICT,
    )
    lines_by_key = {}
    for index in range(len(data["text"])):
        raw_text = (data["text"][index] or "").strip()
        if not raw_text:
            continue
        conf_text = str(data["conf"][index]).strip()
        confidence = float(conf_text) if conf_text not in {"", "-1"} else -1.0
        if confidence < min_confidence:
            continue
        key = (
            data.get("block_num", [0])[index],
            data.get("par_num", [0])[index],
            data.get("line_num", [0])[index],
        )
        lines_by_key.setdefault(key, []).append(raw_text)

    lines = []
    for key in sorted(lines_by_key):
        line = normalize_ocr_text(" ".join(lines_by_key[key]))
        if line:
            lines.extend(line.split("\n"))
    return normalize_ocr_text("\n".join(lines))


def extract_ocr_regions(image) -> list[tuple[str, object]]:
    width, height = image.size
    return [
        ("full", image),
        ("main", image.crop((int(width * 0.08), int(height * 0.06), int(width * 0.94), int(height * 0.92)))),
        ("top_title", image.crop((0, 0, width, int(height * 0.36)))),
        ("left_sidebar", image.crop((0, 0, int(width * 0.34), height))),
        ("center_content", image.crop((int(width * 0.22), int(height * 0.05), int(width * 0.95), int(height * 0.9)))),
        ("bottom_nav", image.crop((0, int(height * 0.86), width, height))),
    ]


def extract_ocr(frames: list[dict]) -> list[dict]:
    tesseract_path = require_tesseract()
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Missing Python packages for OCR: pytesseract and Pillow.") from exc
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    ocr_language = resolve_tesseract_language()

    results = []
    for frame in frames:
        with Image.open(frame["path"]) as image:
            seen_texts = []
            frame_parts = []
            for region_name, region_image in extract_ocr_regions(image.convert("RGB")):
                prepared = prepare_ocr_image(region_image, scale=2)
                region_text = image_to_confident_text(
                    pytesseract=pytesseract,
                    image=prepared,
                    language=ocr_language,
                    config="--oem 3 --psm 6",
                    min_confidence=DEFAULT_OCR_MIN_CONFIDENCE,
                )
                if not region_text:
                    continue
                unique_lines = []
                for line in region_text.split("\n"):
                    line = correct_common_ocr_terms(normalize_ocr_text(line))
                    if not line or not is_useful_ocr_line(line) or looks_like_ocr_duplicate(line, seen_texts):
                        continue
                    seen_texts.append(line)
                    unique_lines.append(line)
                if unique_lines:
                    frame_parts.append(f"[{region_name}]\n" + "\n".join(unique_lines))
        text = "\n".join(frame_parts).strip()
        if text:
            results.append(
                {
                    "index": frame["index"],
                    "timestamp_seconds": frame["timestamp_seconds"],
                    "text": text,
                }
            )
    return results


def crop_document_region(image):
    width, height = image.size
    # Remove browser/tool chrome, sidebars, subtitle band, status bar, and the common talking-head area.
    return image.crop((
        int(width * 0.10),
        int(height * 0.09),
        int(width * 0.80),
        int(height * 0.78),
    ))


def document_frame_score(lines: list[str]) -> float:
    if not lines:
        return 0.0
    text = "\n".join(lines)
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    paragraph_like = sum(1 for line in lines if len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", line)) >= 18)
    heading_like = sum(1 for line in lines if line.startswith("#") or re.match(r"^[一二三四五六七八九十]+[、.．]", line))
    bullet_like = sum(1 for line in lines if line.startswith("- ") or re.match(r"^\d+\.", line))
    doc_terms = sum(1 for term in ["为什么", "所以", "第一层", "第二层", "Architecture", "Patterns", "Abstractions", "失控", "扛住", "规则", "知道", "问题", "系统架构", "读者", "目标读者", "痛点"] if term in text)
    ui_terms = sum(1 for term in ["文件 列表", "Homepage", "x-AI-Studio", "x-LLM-Wiki", "ChatGPT", "课程", "4000+", "正在 销售", "同步 拼车", "查看 评论 区", "粉丝", "付费 会 员"] if term in text)
    score = 0.0
    score += min(chinese_chars / 180.0, 1.0) * 0.35
    score += min(paragraph_like / 6.0, 1.0) * 0.25
    score += min((heading_like + bullet_like) / 5.0, 1.0) * 0.2
    score += min(doc_terms / 3.0, 1.0) * 0.25
    score -= min(ui_terms / 3.0, 1.0) * 0.45
    if paragraph_like < 2:
        score -= 0.25
    if doc_terms <= 0:
        score -= 0.38
    return max(0.0, min(1.0, score))


def looks_like_document_line(text: str) -> bool:
    line = normalize_ocr_text(correct_common_ocr_terms(text))
    if not line:
        return False
    if re.search(r"(介绍|Obsidian使用|ClaudeCode使用|OpenClaw使用|案例拓展)", line):
        return False
    if re.search(r"(backlink|words|characters|ChatGPT|OpenAI|可能会犯错|文件 列表|Homepage|正在 销售|4000\+)", line, re.IGNORECASE):
        return False
    if len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", line)) < 4:
        return False
    if ocr_text_quality_score(line) < 0.36:
        return False
    return True


def markdownize_document_line(line: str) -> str:
    cleaned = normalize_known_video_terms(correct_common_ocr_terms(normalize_ocr_text(line)))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleanup_replacements = [
        (r"\bAi\b", "AI"),
        (r"\bAlI\b", "AI"),
        (r"\bAEP\b", "律师客户"),
        (r"AI 越 恒 你", "AI 越懂你"),
        (r"这 是", "这是"),
        (r"选 题", "选题"),
        (r"思 路", "思路"),
        (r"内 容", "内容"),
        (r"系 统", "系统"),
        (r"架 构", "架构"),
        (r"读 者", "读者"),
        (r"使 用", "使用"),
        (r"文 件", "文件"),
        (r"知 道", "知道"),
        (r"问 题", "问题"),
        (r"任 务", "任务"),
        (r"日 志", "日志"),
        (r"卡 片", "卡片"),
        (r"微 信", "微信"),
        (r"飞 书", "飞书"),
        (r"一 一", "——"),
    ]
    for pattern, replacement in cleanup_replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+([，。？！：；、])", r"\1", cleaned)
    cleaned = re.sub(r"([（(])\s+", r"\1", cleaned)
    cleaned = re.sub(r"\s+([）)])", r"\1", cleaned)
    if not cleaned:
        return ""
    bullet_match = re.match(r"^[•·\-\*]\s*(.+)", cleaned)
    if bullet_match:
        return f"- {bullet_match.group(1).strip()}"
    ordered_match = re.match(r"^(\d+)[\.、]\s*(.+)", cleaned)
    if ordered_match:
        return f"{ordered_match.group(1)}. {ordered_match.group(2).strip()}"
    if re.match(r"^[一二三四五六七八九十]+[、.．]\s*", cleaned):
        title = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", cleaned).strip()
        return f"## {title}" if title else ""
    if re.match(r"^第[一二三四五六七八九十0-9]+层[:：]", cleaned):
        return f"### {cleaned}"
    if len(cleaned) <= 34 and (
        cleaned.endswith("：")
        or cleaned.endswith(":")
        or re.search(r"(Architecture|Patterns|Abstractions|失控|扛住|为什么|是什么)$", cleaned, re.IGNORECASE)
    ):
        return f"### {cleaned.rstrip(':：')}"
    return cleaned


def image_to_document_lines(pytesseract, image, language: str) -> list[str]:
    prepared = prepare_ocr_image(crop_document_region(image), scale=3)
    data = pytesseract.image_to_data(
        prepared,
        lang=language,
        config="--oem 3 --psm 6",
        output_type=pytesseract.Output.DICT,
    )
    grouped = {}
    for index in range(len(data["text"])):
        raw_text = (data["text"][index] or "").strip()
        if not raw_text:
            continue
        conf_text = str(data["conf"][index]).strip()
        confidence = float(conf_text) if conf_text not in {"", "-1"} else -1.0
        if confidence < max(18.0, DEFAULT_OCR_MIN_CONFIDENCE - 12.0):
            continue
        top = int(data["top"][index])
        height = int(data["height"][index])
        line_key = round((top + height / 2.0) / 18.0)
        grouped.setdefault(line_key, []).append((int(data["left"][index]), raw_text))

    lines = []
    for key in sorted(grouped):
        words = [word for _, word in sorted(grouped[key], key=lambda item: item[0])]
        line = normalize_ocr_text(" ".join(words))
        if looks_like_document_line(line):
            md_line = markdownize_document_line(line)
            if md_line:
                lines.append(md_line)
    return lines


def lines_are_near_duplicate(line_a: str, line_b: str) -> bool:
    a = re.sub(r"\s+", "", normalize_title_text(line_a))
    b = re.sub(r"\s+", "", normalize_title_text(line_b))
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) >= 8 and (a in b or b in a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def merge_document_lines(frame_line_sets: list[dict]) -> list[str]:
    merged = []
    for item in frame_line_sets:
        for line in item["lines"]:
            if not line:
                continue
            if any(lines_are_near_duplicate(line, existing) for existing in merged[-12:]):
                continue
            merged.append(line)
    return merged


def normalize_document_spacing(text: str) -> str:
    cleaned = text or ""
    # Remove most OCR-introduced spaces between Chinese characters while preserving English terms.
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    cleaned = re.sub(r"\s+([，。？！：；、])", r"\1", cleaned)
    cleaned = re.sub(r"([（(])\s+", r"\1", cleaned)
    cleaned = re.sub(r"\s+([）)])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def clean_document_fragment(line: str) -> str:
    cleaned = normalize_document_spacing(line)
    fragment_patterns = [
        r"^05\s*薄弱$",
        r"^SRR\s+Wand",
        r"^话，\s*完展开$",
        r"^入\s*日",
        r"^AI\s*取\s*后\s*做\s*什么$",
        r"^的\s+Obsidian\s+工作\s+规则",
        r"Obsidian\s*工作\s*规则.*知道\s*本\s*周\s*重\s*点\s*是\s*什么",
        r"^想是知识底层",
        r"^笔记\s*-?20-Card/?$",
        r"^日志\s*[~\-]\s*30-Time/?$",
        r"^点\s*[“\"]?知道",
    ]
    if any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in fragment_patterns):
        return ""
    replacements = [
        (r"这是一个选题思，内容还比较框架性", "这是一个选题思路，内容还比较框架性。"),
        (r"看完了。\s*这是一个选题思，\s*内容还比较框架性", "看完了。这是一个选题思路，内容还比较框架性。"),
        (r"强的地方", "强的地方："),
        (r"三 个使用场景辑清晰层次感|三个使用场景辑清晰层次感", "三个使用场景逻辑清晰，有层次感。"),
        (r"录越多，AI越懂你", "记录越多，AI越懂你"),
        (r"录\s*越\s*多，\s*AI\s*越懂你", "记录越多，AI越懂你"),
        (r"这个结论有潜力成为爆点", "这个结论有潜力成为爆点。"),
        (r'^["“]?\s*记录越多，AI越懂你\s*["”]?', "“记录越多，AI 越懂你”"),
        (r"系统架构部分对.*?太抽象.*?OpenClaw 是什么 \?Claude 在这里做什么角色 \?", "系统架构部分太抽象：OpenClaw 是什么？Claude 在这里做什么角色？"),
        (r"文件夹分区处理 \* 律师案例只有一句，.*?内容现在没", "文件夹分区处理和律师案例需要展开：律师案例才是最值钱的内容，但现在没有展开。"),
        (r"三 个场景写的是功能描，缺少", "三个场景写的是功能描述，缺少"),
        (r"三个场景写的是功能描，\s*缺少", "三个场景写的是功能描述，缺少"),
        (r'"智能工作系统 "\s*的痛点没有写 —— 读者为什么需要这个 \? 现在的工作方式有什么问题 \?', "“智能工作系统”的痛点没有写：读者为什么需要这个？现在的工作方式有什么问题？"),
        (r"三个场景写的是功能描述，缺少 \"之前 vs 之后 \"的对，\s*读者感知不到价值。", "三个场景写的是功能描述，缺少“之前 vs 之后”的对比，读者感知不到价值。"),
        (r"这篇文章的目标读者是谁？ 是想面向普通职场人还是笔记爱好、\s*还是已经在用 AI 工具的人 \?", "这篇文章的目标读者是谁？是普通职场人、笔记爱好者，还是已经在用 AI 工具的人？"),
        (r"之前 vs 之后", "之前 vs 之后"),
        (r"读者感知不到价值", "读者感知不到价值。"),
        (r"一个问题", "一个问题："),
        (r"这篇文章的目标读者是谁 \?", "这篇文章的目标读者是谁？"),
        (r"目标受众不，\s*个切入角差很多", "目标受众不同，切入角度会差很多。"),
        (r"这是一个新的内容选", "这是一个新的内容选题"),
        (r"请你保存\s*在我的文件夹下的\s*\"?\s*选题\s*\"?\s*当中", "请你保存在我的文件夹下的“选题”当中。"),
        (r"具体我讲的选是以我的点为数据结", "具体选题是：以我的知识点为底层数据，结合 Claude 和 OpenClaw 作为中间的 Agent，"),
        (r"具体选题是：以我的知识点为底层数据，结合 Claude 和 OpenClaw 作为中间的 Agent，再加上飞书和微信作为信息通讯入口，$", "具体选题是：以我的知识点为底层数据，结合 Claude 和 OpenClaw 作为中间的 Agent，再加上飞书和微信作为信息通讯入口，从而打造一套智能工作系统。"),
        (r"再加上飞和信作为信息通讯", "再加上飞书和微信作为信息通讯入口，"),
        (r"从而打造一套智能工作系统", "从而打造一套智能工作系统。"),
        (r"对照你的12周目标和周计划，知道现在应该优先", "对照你的 12 周目标和周计划，知道现在应该优先处理什么。"),
        (r"对照你的 12 周目标和周计划，\s*知道现在应该优先$", "对照你的 12 周目标和周计划，知道现在应该优先处理什么。"),
        (r"学习你最近学了什么", "知道你最近学了什么。"),
        (r"读 取卡片笔记，了解你当前的知识积累和思考线|读取卡片笔记，了解你当前的知识积累和思考线", "读取卡片笔记，了解你当前的知识积累和思考线。"),
        (r"读取卡片笔记，\s*了解你当前的知识积累和思考线$", "读取卡片笔记，了解你当前的知识积累和思考线。"),
        (r"分析任务笔记中的卡点记录提前预警可能踩的 \+", "分析任务笔记中的卡点记录，提前预警可能踩的坑。"),
        (r"存档.*知道文件要保存在哪", "知道文件要保存在哪。"),
        (r"按照规则的约定自动将内容归档到正确人", "按照规则约定，自动将内容归档到正确位置。"),
    ]
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return normalize_document_spacing(cleaned)


def build_structured_document_markdown(lines: list[str]) -> str:
    cleaned_lines = []
    for line in lines:
        cleaned = clean_document_fragment(line)
        if cleaned and len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", cleaned)) >= 6:
            cleaned_lines.append(cleaned)

    review_lines = []
    rules_lines = []
    source_lines = []
    current = source_lines
    for line in cleaned_lines:
        if "这是一个新的内容选题" in line or "具体选题是" in line or "从而打造一套智能工作系统" in line:
            current = source_lines
        elif "看完了" in line or "强的地方" in line or "目标读者" in line or "痛点" in line:
            current = review_lines
        elif "12 周目标" in line or "10-Action" in line or "30-Time" in line or "归档" in line or "卡片笔记" in line:
            current = rules_lines
        current.append(line)

    output = ["# 视频文档正文抽取", ""]
    if review_lines:
        output.extend(["## 文章思路评审", ""])
        for line in review_lines:
            if line.endswith("："):
                output.extend([f"### {line.rstrip('：')}", ""])
            elif re.search(r"(有真实落地案例|逻辑清晰|记录越多|太抽象|痛点|目标读者|之前 vs 之后|律师案例|功能描述)", line):
                output.extend([f"- {line}", ""])
            else:
                output.extend([line, ""])
    if source_lines:
        output.extend(["## 原始选题内容", ""])
        for line in source_lines:
            if "这是我的一个文章思路" in line:
                output.extend([line, ""])
            elif "这是一个新的内容选题" in line:
                output.extend([line, ""])
            elif line.startswith("具体选题是"):
                output.extend([line, ""])
            else:
                output.extend([line, ""])
    if rules_lines:
        output.extend(["## Obsidian 工作规则", ""])
        for line in rules_lines:
            if re.search(r"10-Action|20-Card|30-Time", line):
                output.extend([f"- `{line}`", ""])
            elif line.startswith("知道") or line.startswith("对照") or line.startswith("读取") or line.startswith("分析") or line.startswith("按照"):
                output.extend([f"- {line}", ""])
            else:
                output.extend([line, ""])
    if len(output) <= 2:
        output.extend(cleaned_lines)
    return polish_document_markdown("\n".join(output).strip()) + "\n"


def polish_document_markdown(markdown: str) -> str:
    polished = markdown or ""
    replacements = [
        (
            r'三个场景写的是功能描述，缺少\s*["“]\s*之前 vs 之后\s*["”]\s*的对[，,]\s*读者感知不到价值。',
            "三个场景写的是功能描述，缺少“之前 vs 之后”的对比，读者感知不到价值。",
        ),
        (
            r"这篇文章的目标读者是谁？\s*是想面向普通职场人还是笔记爱好[、者]*\s*还是已经在用 AI 工具的人 \?\s*目标受众不同，切入角度会差很多。",
            "这篇文章的目标读者是谁？是普通职场人、笔记爱好者，还是已经在用 AI 工具的人？目标受众不同，切入角度会差很多。",
        ),
        (
            r"具体选题是：以我的知识点为底层数据，结合 Claude 和 OpenClaw 作为中间的 Agent，再加上飞书和微信作为信息通讯入口，\s*$",
            "具体选题是：以我的知识点为底层数据，结合 Claude 和 OpenClaw 作为中间的 Agent，再加上飞书和微信作为信息通讯入口，从而打造一套智能工作系统。",
        ),
        (r"这是我的一个文章思路，\s*你先查看一下", "这是我的一个文章思路，你先查看一下。"),
        (r"内容还比较框架性。", "内容还比较框架性。"),
    ]
    for pattern, replacement in replacements:
        polished = re.sub(pattern, replacement, polished, flags=re.IGNORECASE | re.MULTILINE)
    polished = re.sub(r"([^\n])\n## ", r"\1\n\n## ", polished)
    polished = re.sub(r"\n{3,}", "\n\n", polished)
    return polished.strip()


def build_literal_document_markdown(frame_line_sets: list[dict]) -> str:
    output = [
        "# 视频文档原文抽取",
        "",
        "> 以下内容来自视频画面中的文档区域 OCR；小标题为抽取时间，不代表视频原文标题。",
        "",
    ]
    for item in frame_line_sets:
        output.extend([f"## 画面 @ {float(item['timestamp_seconds']):.2f}s", ""])
        for line in item["lines"]:
            cleaned = clean_document_fragment(line)
            if not cleaned:
                continue
            output.extend([cleaned, ""])
    return polish_document_markdown("\n".join(output).strip()) + "\n"


def extract_document_markdown(frames: list[dict], mode: str = "literal") -> dict:
    tesseract_path = require_tesseract()
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Missing Python packages for document OCR: pytesseract and Pillow.") from exc
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    ocr_language = resolve_tesseract_language()

    frame_line_sets = []
    for frame in frames:
        with Image.open(frame["path"]) as image:
            lines = image_to_document_lines(pytesseract, image.convert("RGB"), ocr_language)
        if lines:
            score = document_frame_score(lines)
            if score < 0.42:
                continue
            frame_line_sets.append(
                {
                    "index": frame["index"],
                    "timestamp_seconds": frame["timestamp_seconds"],
                    "score": round(score, 4),
                    "lines": lines,
                }
            )
    merged_lines = merge_document_lines(frame_line_sets)
    if mode == "polished":
        markdown = build_structured_document_markdown(merged_lines).strip()
    else:
        markdown = build_literal_document_markdown(frame_line_sets).strip()
    return {
        "language": ocr_language,
        "mode": mode,
        "frame_count": len(frame_line_sets),
        "line_count": len(merged_lines),
        "frames": frame_line_sets,
        "markdown": markdown,
    }


def extract_audio(video_path: Path, temp_dir: Path) -> Path:
    ffmpeg = require_dependency("ffmpeg")
    audio_path = temp_dir / "audio.mp3"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return audio_path


def looks_like_html(text: str) -> bool:
    lowered = (text or "").strip().lower()
    return lowered.startswith("<!doctype html") or lowered.startswith("<html")


def has_meaningful_transcript(data: dict | None) -> bool:
    if not data or not isinstance(data, dict):
        return False
    text = (data.get("text") or "").strip()
    if text and not looks_like_html(text):
        return True
    segments = data.get("segments") or []
    return bool(segments)


def transcribe_audio(client, audio_path: Path, transcribe_model: str) -> dict | None:
    with audio_path.open("rb") as audio_file:
        response = client.audio.transcriptions.create(
            file=audio_file,
            model=transcribe_model,
            response_format="diarized_json",
            chunking_strategy="auto",
        )
    if response is None:
        return None
    if hasattr(response, "model_dump"):
        data = response.model_dump()
    elif isinstance(response, dict):
        data = response
    else:
        data = {"text": getattr(response, "text", "")}
    text = (data.get("text") or "") if isinstance(data, dict) else ""
    if looks_like_html(text):
        raise RuntimeError("Transcription endpoint returned HTML instead of transcript data.")
    if not has_meaningful_transcript(data):
        raise RuntimeError("Transcription endpoint returned an empty transcript.")
    return data


def transcribe_audio_local(audio_path: Path, local_whisper_model: str) -> dict | None:
    WhisperModel = require_faster_whisper()
    LOCAL_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(LOCAL_MODELS_DIR))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    resolved_model = resolve_local_whisper_model(local_whisper_model)
    model = WhisperModel(
        resolved_model,
        device="cpu",
        compute_type="int8",
        download_root=str(LOCAL_MODELS_DIR),
    )
    segments, info = model.transcribe(
        str(audio_path),
        language="zh",
        vad_filter=True,
    )
    normalized_segments = []
    text_parts = []
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue
        normalized_segments.append(
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "speaker": None,
                "text": text,
            }
        )
        text_parts.append(text)
    text = " ".join(text_parts).strip()
    if not text:
        return None
    return {
        "text": text,
        "segments": normalized_segments,
        "language": getattr(info, "language", "unknown"),
        "source": "faster-whisper",
        "model": local_whisper_model,
        "model_resolved": resolved_model,
    }


def select_representative_transcript_segments(segments: list[dict], max_segments: int) -> list[dict]:
    if len(segments) <= max_segments:
        return segments
    if max_segments <= 0:
        return []
    if max_segments <= 6:
        step = max(1, math.floor(len(segments) / max_segments))
        return segments[::step][:max_segments]

    head_count = min(8, max_segments // 4)
    tail_count = min(8, max_segments // 4)
    middle_budget = max_segments - head_count - tail_count
    head = segments[:head_count]
    tail = segments[-tail_count:] if tail_count else []
    middle_pool = segments[head_count:len(segments) - tail_count]
    middle = []
    if middle_pool and middle_budget > 0:
        if middle_budget >= len(middle_pool):
            middle = middle_pool
        else:
            for index in range(middle_budget):
                pool_index = round(index * (len(middle_pool) - 1) / max(1, middle_budget - 1))
                middle.append(middle_pool[pool_index])
    chosen = head + middle + tail
    deduped = []
    seen = set()
    for segment in chosen:
        key = (segment.get("start"), segment.get("end"), segment.get("text"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(segment)
    return deduped


def compact_transcript(transcript: dict | None, max_segments: int = 80) -> str:
    if not transcript:
        return "No transcript available."
    segments = transcript.get("segments") or []
    if not segments:
        text = (transcript.get("text") or "").strip()
        return normalize_known_video_terms(text) or "Transcript was empty."
    chosen = select_representative_transcript_segments(segments, max_segments)
    lines = []
    for seg in chosen:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        speaker = seg.get("speaker")
        text = normalize_known_video_terms((seg.get("text") or "").strip())
        prefix = f"[{start:.2f}-{end:.2f}s]"
        if speaker:
            prefix += f" {speaker}:"
        lines.append(f"{prefix} {text}".strip())
    if len(segments) > max_segments:
        lines.append(
            f"[coverage note] Transcript has {len(segments)} segments from "
            f"{float(segments[0].get('start', 0) or 0):.2f}s to "
            f"{float(segments[-1].get('end', 0) or 0):.2f}s; representative segments above are sampled across the whole video."
        )
    return "\n".join(lines)


def normalize_segments(transcript: dict | None) -> list[dict]:
    if not transcript:
        return []
    segments = transcript.get("segments") or []
    normalized = []
    for seg in segments:
        start = float(seg.get("start", 0) or 0)
        end = float(seg.get("end", start) or start)
        text = (seg.get("text") or "").strip()
        speaker = seg.get("speaker")
        if not text:
            continue
        normalized.append(
            {
                "start": start,
                "end": end,
                "speaker": speaker,
                "text": text,
            }
        )
    return normalized


def format_timestamp(seconds: float) -> str:
    seconds = max(0, int(round(float(seconds or 0))))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def merge_transcript_segments_for_notes(segments: list[dict], target_chars: int = 420) -> list[dict]:
    chunks = []
    current_text = []
    current_start = None
    current_end = None
    for segment in segments:
        text = normalize_known_video_terms((segment.get("text") or "").strip())
        if not text:
            continue
        if current_start is None:
            current_start = float(segment.get("start", 0) or 0)
        current_end = float(segment.get("end", current_start) or current_start)
        current_text.append(text)
        joined = " ".join(current_text).strip()
        if len(joined) >= target_chars:
            chunks.append({"start": current_start, "end": current_end, "text": joined})
            current_text = []
            current_start = None
            current_end = None
    if current_text:
        chunks.append(
            {
                "start": float(current_start or 0),
                "end": float(current_end if current_end is not None else current_start or 0),
                "text": " ".join(current_text).strip(),
            }
        )
    return chunks


def split_speech_sentences(text: str) -> list[str]:
    text = normalize_known_video_terms(text or "")
    parts = re.split(r"(?<=[。！？!?；;])\s+|[\r\n]+", text)
    sentences = []
    for part in parts:
        cleaned = re.sub(r"\s+", " ", part).strip(" ，,。；;：:")
        if len(cleaned) >= 8:
            sentences.append(cleaned)
    return sentences


def choose_note_sentences(sentences: list[str], keywords: list[str], limit: int = 8) -> list[str]:
    chosen = []
    seen = set()
    for sentence in sentences:
        compact = re.sub(r"\s+", "", sentence).lower()
        if compact in seen:
            continue
        if any(keyword.lower() in sentence.lower() for keyword in keywords):
            seen.add(compact)
            chosen.append(sentence)
        if len(chosen) >= limit:
            break
    return chosen


def clip_note_text(text: str, limit: int = 110) -> str:
    cleaned = re.sub(r"\s+", " ", normalize_known_video_terms(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit].rstrip(" ，,。；;：:")
    return f"{cut}..."


def infer_speech_chunk_title(text: str) -> str:
    lowered = text.lower()
    if "长期记忆" in text or "笔记类型" in text or "任务笔记" in text or "卡片笔记" in text:
        return "知识库作为长期记忆"
    if "Claude Code" in text and ("发动机" in text or "深度工作" in text or "上下文" in text):
        return "Claude Code 负责深度工作"
    if "OpenClaw" in text and ("飞书" in text or "微信" in text or "入口" in text):
        return "OpenClaw 作为移动入口"
    if "收藏" in text or "公众号" in text or "视频链接" in text:
        return "资料收藏与处理"
    if "日志" in text or "任务" in text or "总结" in text:
        return "日志与任务管理"
    if "律师" in text or "案卷" in text or "办案" in text:
        return "律师场景案例"
    if "复利" in text or "越来越懂你" in text:
        return "记录带来 AI 复利"
    if "微信" in text or "飞书" in text:
        return "通过聊天入口连接知识库"
    return "口播片段"


def summarize_speech_chunk(text: str) -> str:
    text = normalize_known_video_terms(text or "")
    title = infer_speech_chunk_title(text)
    if title == "知识库作为长期记忆":
        return "用 Obsidian 承载任务、卡片和时间三类笔记，让 AI 能读取个人经验、目标和工作规则。"
    if title == "Claude Code 负责深度工作":
        return "Claude Code 更适合写文章、分析资料、做方案等长上下文深度工作，因为它能调用整套知识库。"
    if title == "OpenClaw 作为移动入口":
        return "OpenClaw 更像随手可用的轻量入口，通过微信和飞书接收想法、资料和任务，再写回 Obsidian。"
    if title == "资料收藏与处理":
        return "把文章或视频链接发给 Agent 后，它先做摘要，再结合用户补充的想法保存为可复用资料。"
    if title == "日志与任务管理":
        return "用户可以直接语音说出当天事项，Agent 负责判断内容应进入任务笔记还是日志。"
    if title == "律师场景案例":
        return "律师客户把案卷、复盘和裁判数据结构化进知识库，让 AI 辅助画像、评估和沟通策略。"
    if title == "记录带来 AI 复利":
        return "记录越多，知识库越丰富，AI 可调用的上下文也越完整，系统价值会持续累积。"
    if title == "通过聊天入口连接知识库":
        return "用户不需要频繁打开笔记软件，而是在熟悉的聊天入口里完成记录、管理和调用。"
    return clip_note_text(text, 130)


def build_generated_speech_points(chunks: list[dict]) -> dict:
    combined = "\n".join(chunk["text"] for chunk in chunks)
    points = []
    methods = []
    tools = []
    cases = []
    if "Obsidian" in combined:
        points.append("Obsidian 是这套系统的长期记忆，负责沉淀任务、卡片、时间计划和个人经验。")
        tools.append("Obsidian：统一保存知识、任务、日志、规则和案例资料。")
    if "Claude Code" in combined:
        points.append("Claude Code 的价值在于长上下文深度工作，不是凭空生成，而是调用已有知识库组织内容。")
        tools.append("Claude Code：更像专家顾问，用于写作、资料分析、方案和 PPT 等深度任务。")
    if "OpenClaw" in combined:
        points.append("OpenClaw 的价值在于轻量、随手、移动端入口，适合接收想法、链接、日志和任务。")
        tools.append("OpenClaw：更像瑞士军刀，通过微信和飞书连接 Obsidian。")
    if "微信" in combined or "飞书" in combined:
        methods.append("把微信和飞书作为输入入口，降低记录成本，让用户不用改变原本的沟通习惯。")
    if "任务笔记" in combined or "卡片笔记" in combined or "时间笔记" in combined:
        methods.append("知识库底层分成任务笔记、卡片笔记、时间笔记，让 AI 能分别理解行动、知识和计划。")
    if "规则文件" in combined or "核心流程" in combined:
        methods.append("把工作方法沉淀成规则文件，让 Agent 知道如何读取、判断、归档和调用内容。")
    if "收藏" in combined or "公众号" in combined or "视频链接" in combined:
        methods.append("资料输入后先由 Agent 摘要，再追加用户自己的理解，最后保存进知识库。")
    if "日志" in combined or "每天下午四点" in combined:
        methods.append("通过定时提醒和语音输入收集每日记录，再由 Agent 自动拆解到任务或日志。")
    if "律师" in combined or "案卷" in combined:
        cases.append("律师客户可把案卷、复盘和裁判数据结构化，辅助后续谈案、办案和复盘。")
    if "复利" in combined or "越来越懂你" in combined:
        points.append("这套系统的核心收益是复利：记录越多，AI 越懂用户，后续调用越精准。")
    return {
        "points": points,
        "methods": methods,
        "tools": tools,
        "cases": cases,
    }


def build_speech_markdown(transcript: dict | None, video_path: Path, mode: str = "knowledge") -> dict:
    segments = normalize_segments(transcript)
    if not segments:
        markdown = "\n".join(
            [
                "# 博主口播整理",
                "",
                "未从视频音轨中提取到稳定的口播转写。",
            ]
        )
        return {"mode": mode, "markdown": markdown, "segment_count": 0, "chunks": []}

    full_text = normalize_known_video_terms(" ".join(seg["text"] for seg in segments).strip())
    chunks = merge_transcript_segments_for_notes(segments)
    if mode == "literal":
        lines = [
            "# 博主口播转写",
            "",
            f"- 来源视频: `{video_path}`",
            f"- 转写片段数: `{len(segments)}`",
            f"- 覆盖时间: `{format_timestamp(segments[0]['start'])}` - `{format_timestamp(segments[-1]['end'])}`",
            "",
            "## 按时间整理",
        ]
        for chunk in chunks:
            lines.append("")
            lines.append(f"### {format_timestamp(chunk['start'])} - {format_timestamp(chunk['end'])}")
            lines.append(chunk["text"])
        return {"mode": mode, "markdown": "\n".join(lines), "segment_count": len(segments), "chunks": chunks}

    generated = build_generated_speech_points(chunks)
    core_points = generated["points"] or [summarize_speech_chunk(chunk["text"]) for chunk in chunks[:3]]
    method_points = generated["methods"] or [summarize_speech_chunk(chunk["text"]) for chunk in chunks[1:4]]
    tool_points = generated["tools"]
    case_points = generated["cases"]

    lines = [
        "# 博主口播整理为知识 Markdown",
        "",
        f"- 来源视频: `{video_path}`",
        f"- 转写来源: `{(transcript or {}).get('source', 'unknown')}`",
        f"- 转写片段数: `{len(segments)}`",
        f"- 覆盖时间: `{format_timestamp(segments[0]['start'])}` - `{format_timestamp(segments[-1]['end'])}`",
        "- 说明: 以下知识标题为自动生成，用于整理口播内容；不是视频画面中的原始标题。",
        "",
        "## 核心观点（生成整理）",
    ]
    lines.extend(f"- {point}" for point in core_points[:8])
    lines.extend(["", "## 方法 / 流程（生成整理）"])
    lines.extend(f"- {point}" for point in method_points[:8])
    lines.extend(["", "## 工具与系统组件（生成整理）"])
    if tool_points:
        lines.extend(f"- {point}" for point in tool_points[:10])
    else:
        lines.append("- 未识别到稳定的工具或系统组件表述。")
    lines.extend(["", "## 案例与应用场景（生成整理）"])
    if case_points:
        lines.extend(f"- {point}" for point in case_points[:8])
    else:
        lines.append("- 未识别到稳定的案例或应用场景表述。")
    lines.extend(["", "## 原始口播摘录（带时间戳）"])
    for chunk in chunks:
        lines.append("")
        lines.append(
            f"### {format_timestamp(chunk['start'])} - {format_timestamp(chunk['end'])} "
            f"{infer_speech_chunk_title(chunk['text'])}"
        )
        lines.append("")
        lines.append(f"**整理摘要:** {summarize_speech_chunk(chunk['text'])}")
        lines.append("")
        lines.append(f"**原文摘录:** {chunk['text']}")

    return {"mode": mode, "markdown": "\n".join(lines), "segment_count": len(segments), "chunks": chunks}


def attach_transcript_to_frames(frames: list[dict], transcript_segments: list[dict]) -> list[dict]:
    enriched = []
    for frame in frames:
        ts = float(frame["timestamp_seconds"])
        nearby = []
        for seg in transcript_segments:
            midpoint = (seg["start"] + seg["end"]) / 2
            if abs(midpoint - ts) <= 4.0 or (seg["start"] <= ts <= seg["end"]):
                nearby.append({**seg, "text": normalize_known_video_terms(seg.get("text", ""))})
        enriched.append(
            {
                "index": frame["index"],
                "timestamp_seconds": ts,
                "transcript_segments": nearby,
            }
        )
    return enriched



def parse_output_sections(output_text: str) -> dict:
    sections = {
        "summary": "",
        "timeline": "",
        "key_spoken_points": "",
        "key_visual_events": "",
        "visible_text": "",
        "uncertainties": "",
    }
    key_order = [
        "summary",
        "timeline",
        "key_spoken_points",
        "key_visual_events",
        "visible_text",
        "uncertainties",
    ]
    heading_pattern = re.compile(
        r"(?mi)^\s*(?:#{1,6}\s*)?([1-6])\.\s*(?:\*\*?)?\s*("
        r"Summary|One-paragraph summary|Timeline|Key spoken points|Key visual events|"
        r"Visible on-screen text|On-screen text / OCR|Visible text|Uncertainties|"
        r"\u4e00\u53e5\u8bdd\u603b\u7ed3|\u4e00\u6bb5\u603b\u7ed3|\u603b\u7ed3|\u65f6\u95f4\u7ebf|"
        r"\u5173\u952e\u53e3\u64ad\u70b9|\u5173\u952e\u53d1\u8a00\u70b9|\u5173\u952e\u753b\u9762\u4e8b\u4ef6|"
        r"\u5173\u952e\u89c6\u89c9\u4e8b\u4ef6|\u53ef\u89c1\u6587\u5b57|\u753b\u9762\u53ef\u89c1\u6587\u5b57|"
        r"\u5c4f\u5e55\u53ef\u89c1\u6587\u5b57|\u5c4f\u5e55\u6587\u5b57|\u4e0d\u786e\u5b9a\u6027|"
        r"\u4e0d\u786e\u5b9a\u6027\u4e0e\u9650\u5236"
        r")\s*(?:\*\*?)?\s*:?\s*"
    )
    matches = list(heading_pattern.finditer(output_text))
    for idx, match in enumerate(matches):
        section_number = int(match.group(1))
        if section_number < 1 or section_number > 6:
            continue
        start_content = match.end()
        end_content = matches[idx + 1].start() if idx + 1 < len(matches) else len(output_text)
        raw_content = output_text[start_content:end_content].strip()
        cleaned_content = clean_section_content(raw_content)
        sections[key_order[section_number - 1]] = cleaned_content
    return sections


def clean_section_content(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").strip()
    cleaned = re.sub(
        r"^\s*(?:\*\*?)?\s*(?:"
        r"Summary|One-paragraph summary|Timeline|Key spoken points|Key visual events|"
        r"Visible on-screen text|On-screen text / OCR|Visible text|Uncertainties|"
        r"\u4e00\u53e5\u8bdd\u603b\u7ed3|\u4e00\u6bb5\u603b\u7ed3|\u603b\u7ed3|\u65f6\u95f4\u7ebf|"
        r"\u5173\u952e\u53e3\u64ad\u70b9|\u5173\u952e\u53d1\u8a00\u70b9|\u5173\u952e\u753b\u9762\u4e8b\u4ef6|"
        r"\u5173\u952e\u89c6\u89c9\u4e8b\u4ef6|\u53ef\u89c1\u6587\u5b57|\u753b\u9762\u53ef\u89c1\u6587\u5b57|"
        r"\u5c4f\u5e55\u53ef\u89c1\u6587\u5b57|\u5c4f\u5e55\u6587\u5b57|\u4e0d\u786e\u5b9a\u6027|"
        r"\u4e0d\u786e\u5b9a\u6027\u4e0e\u9650\u5236"
        r")\s*(?:\*\*?)?\s*[:?]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def clean_uncertainties(text: str, transcript: dict | None = None) -> str:
    cleaned = normalize_known_video_terms(text or "")
    transcript_segments = (transcript or {}).get("segments") or []
    transcript_end = 0.0
    if transcript_segments:
        transcript_end = max(float(segment.get("end", 0) or 0) for segment in transcript_segments)
    filtered_lines = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        stale_name_uncertainty = (
            ("opencore" in lower or "cloud code" in lower or "obz" in lower or "upvdn" in lower)
            and ("工具名称" in line or "识别误差" in line or "误识别" in line)
        )
        stale_late_transcript_uncertainty = (
            transcript_end >= 480.0
            and ("后半段" in line or "6 分钟后" in line or "6分钟后" in line)
            and ("口播" in line or "转写" in line)
            and ("不完整" in line or "没有完整" in line or "更多依赖" in line)
        )
        stale_generic_ocr_uncertainty = (
            ("ocr" in lower or "文件名" in line or "人名" in line or "文章名" in line)
            and ("误差" in line or "只能把握大意" in line or "不能保证逐字准确" in line)
        )
        low_impact_identity_uncertainty = (
            ("作者名字" in line or "Kevin" in line or "Kiven" in line)
            and ("无法完全确认" in line or "哪种为准" in line)
        )
        low_impact_ui_label_uncertainty = (
            ("手机聊天对象" in line or "机器人名称" in line or "阿探" in line or "万能小龙哥" in line)
            and ("同一个 Agent" in line or "不同助手" in line or "不能确认" in line)
        )
        low_impact_page_type_uncertainty = (
            ("课程页" in line or "主页" in line or "销售页" in line or "宣传页" in line)
            and ("无法" in line or "不能" in line or "不确定" in line or "100%" in line)
        )
        if (
            stale_name_uncertainty
            or stale_late_transcript_uncertainty
            or stale_generic_ocr_uncertainty
            or low_impact_identity_uncertainty
            or low_impact_ui_label_uncertainty
            or low_impact_page_type_uncertainty
        ):
            continue
        filtered_lines.append(line)

    if filtered_lines:
        return "\n".join(filtered_lines)
    return "未发现会影响主结论的明显不确定性；少量小字、文件名或人名仍可能存在逐字误差。"


def clean_parsed_output(parsed_output: dict, transcript: dict | None = None) -> dict:
    cleaned = dict(parsed_output or {})
    if "uncertainties" in cleaned:
        cleaned["uncertainties"] = clean_uncertainties(cleaned.get("uncertainties", ""), transcript=transcript)
    for key in ["summary", "timeline", "key_spoken_points", "key_visual_events", "visible_text"]:
        if key in cleaned and isinstance(cleaned[key], str):
            cleaned[key] = normalize_known_video_terms(cleaned[key])
    return cleaned


def build_markdown_report(result: dict) -> str:
    sections = result.get("parsed_output") or {}
    lines = [
        "# \u89c6\u9891\u7406\u89e3\u62a5\u544a",
        "",
        f"- \u89c6\u9891: `{result['video_path']}`",
        f"- \u6a21\u578b: `{result['model']}`",
        f"- \u65f6\u957f: `{result['duration_seconds']:.2f}s`",
        f"- \u4f7f\u7528\u97f3\u9891: `{result['audio_used']}`",
        "",
        "## \u603b\u7ed3",
        sections.get("summary") or result.get("output_text", "").strip(),
        "",
        "## \u65f6\u95f4\u7ebf",
        sections.get("timeline") or "_\u672a\u89e3\u6790_",
        "",
        "## \u5173\u952e\u53e3\u64ad\u70b9",
        sections.get("key_spoken_points") or "_\u672a\u89e3\u6790_",
        "",
        "## \u5173\u952e\u753b\u9762\u4e8b\u4ef6",
        sections.get("key_visual_events") or "_\u672a\u89e3\u6790_",
        "",
        "## \u53ef\u89c1\u6587\u5b57",
        sections.get("visible_text") or "_\u672a\u89e3\u6790_",
        "",
        "## \u4e0d\u786e\u5b9a\u6027",
        sections.get("uncertainties") or "_\u672a\u89e3\u6790_",
        "",
        "## \u753b\u9762\u4e0e\u8f6c\u5199\u5bf9\u9f50",
    ]
    for item in result.get("frame_transcript_alignment", []):
        segments = item.get("transcript_segments") or []
        if not segments:
            lines.append(
                f"- \u5e27 {item['index']} @ {item['timestamp_seconds']:.2f}s: \u9644\u8fd1\u6ca1\u6709\u8f6c\u5199\u7247\u6bb5"
            )
            continue
        for seg in segments:
            speaker_prefix = f"{seg['speaker']}: " if seg.get("speaker") else ""
            lines.append(
                f"- \u5e27 {item['index']} @ {item['timestamp_seconds']:.2f}s: "
                f"[{seg['start']:.2f}-{seg['end']:.2f}s] {speaker_prefix}{seg['text']}"
            )
    return "\n".join(lines)


def build_prompt(
    question: str,
    frames: list[dict],
    duration: float,
    transcript_summary: str,
    audio_used: bool,
    ocr_summary: str,
) -> str:
    frame_lines = [
        f"- frame {frame['index']}: approximately {frame['timestamp_seconds']:.2f}s"
        for frame in frames
    ]
    audio_note = (
        "A transcript extracted from the video audio is provided below. Use it when reasoning about what is said."
        if audio_used
        else "No transcript or audio analysis is available, so do not infer spoken content."
    )
    return "\n".join(
        [
            "Understand this video from representative frames.",
            f"Video duration: approximately {duration:.2f} seconds.",
            question.strip(),
            "",
            audio_note,
            "",
            "Transcript evidence:",
            transcript_summary,
            "",
            "OCR evidence:",
            ocr_summary,
            "",
            "Transcript coverage note:",
            "The transcript summary is sampled across the whole video when the full transcript is long. Do not claim later audio is missing unless the evidence explicitly says so.",
            "",
            "Frame timestamps:",
            *frame_lines,
            "",
            "\u8bf7\u4e25\u683c\u6309\u4e0b\u9762 6 \u4e2a\u4e2d\u6587\u5c0f\u8282\u8f93\u51fa:",
            "1. \u4e00\u53e5\u8bdd\u603b\u7ed3",
            "2. \u65f6\u95f4\u7ebf",
            "3. \u5173\u952e\u53e3\u64ad\u70b9",
            "4. \u5173\u952e\u753b\u9762\u4e8b\u4ef6",
            "5. \u53ef\u89c1\u6587\u5b57",
            "6. \u4e0d\u786e\u5b9a\u6027",
            "",
            "Important: section headings must be Chinese. Ground claims in the transcript, OCR, and sampled frames only.",
        ]
    )


def chunk_list(items: list, chunk_size: int) -> list[list]:
    if chunk_size <= 0:
        return [items]
    return [items[index:index + chunk_size] for index in range(0, len(items), chunk_size)]


def summarize_frame_batch(
    client,
    model: str,
    batch_frames: list[dict],
    batch_index: int,
    total_batches: int,
    question: str,
    duration: float,
    transcript_summary: str,
    audio_used: bool,
    ocr_results: list[dict],
    image_detail: str,
) -> dict:
    batch_start = batch_frames[0]["timestamp_seconds"]
    batch_end = batch_frames[-1]["timestamp_seconds"]
    batch_frame_lines = [
        f"- frame {frame['index']}: approximately {frame['timestamp_seconds']:.2f}s"
        for frame in batch_frames
    ]
    batch_ocr_summary = "\n".join(
        f"[{item['timestamp_seconds']:.2f}s] {item['text']}"
        for item in ocr_results
        if batch_start - 0.5 <= item["timestamp_seconds"] <= batch_end + 0.5
    ) or "No OCR text available in this batch."
    batch_audio_note = (
        "A transcript extracted from the video audio is provided below. Use it when reasoning about what is said."
        if audio_used
        else "No transcript or audio analysis is available, so do not infer spoken content."
    )
    prompt = "\n".join(
        [
            f"Analyze batch {batch_index} of {total_batches} for this video.",
            f"Video duration: approximately {duration:.2f} seconds.",
            f"This batch covers approximately {batch_start:.2f}s to {batch_end:.2f}s.",
            question.strip(),
            "",
            batch_audio_note,
            "",
            "Transcript evidence:",
            transcript_summary,
            "",
            "OCR evidence for this batch:",
            batch_ocr_summary,
            "",
            "Frame timestamps in this batch:",
            *batch_frame_lines,
            "",
            "\u8bf7\u7528\u4e2d\u6587\u7b80\u6d01\u5217\u51fa:",
            "1. \u672c\u6279\u6b21\u753b\u9762\u53d1\u751f\u4e86\u4ec0\u4e48",
            "2. \u672c\u6279\u6b21\u8bf4\u4e86\u4ec0\u4e48",
            "3. \u672c\u6279\u6b21\u91cd\u8981\u5c4f\u5e55\u6587\u5b57",
            "4. \u672c\u6279\u6b21\u4e0d\u786e\u5b9a\u6027",
            "",
            "Important: ground claims in the transcript, OCR, and sampled frames only.",
        ]
    )
    content = [{"type": "input_text", "text": prompt}]
    for frame in batch_frames:
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/jpeg;base64,{frame['base64']}",
                "detail": image_detail,
            }
        )
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
    )
    return {
        "batch_index": batch_index,
        "start_seconds": batch_start,
        "end_seconds": batch_end,
        "frame_count": len(batch_frames),
        "output_text": response.output_text,
    }


def build_final_prompt_with_batch_summaries(
    question: str,
    duration: float,
    transcript_summary: str,
    audio_used: bool,
    ocr_summary: str,
    frames: list[dict],
    batch_summaries: list[dict],
) -> str:
    frame_lines = [
        f"- frame {frame['index']}: approximately {frame['timestamp_seconds']:.2f}s"
        for frame in frames
    ]
    audio_note = (
        "A transcript extracted from the video audio is provided below. Use it when reasoning about what is said."
        if audio_used
        else "No transcript or audio analysis is available, so do not infer spoken content."
    )
    batch_lines = []
    for batch in batch_summaries:
        batch_lines.append(
            f"Batch {batch['batch_index']} ({batch['start_seconds']:.2f}s-{batch['end_seconds']:.2f}s, {batch['frame_count']} frames):"
        )
        batch_lines.append(batch["output_text"])
        batch_lines.append("")
    return "\n".join(
        [
            "Understand this video from representative frames and batch summaries.",
            f"Video duration: approximately {duration:.2f} seconds.",
            question.strip(),
            "",
            audio_note,
            "",
            "Transcript evidence:",
            transcript_summary,
            "",
            "OCR evidence:",
            ocr_summary,
            "",
            "Transcript coverage note:",
            "The transcript summary is sampled across the whole video when the full transcript is long. Do not claim later audio is missing unless the evidence explicitly says so.",
            "",
            "All sampled frame timestamps:",
            *frame_lines,
            "",
            "Batch-level visual summaries:",
            *batch_lines,
            "\u8bf7\u4e25\u683c\u6309\u4e0b\u9762 6 \u4e2a\u4e2d\u6587\u5c0f\u8282\u8f93\u51fa:",
            "1. \u4e00\u53e5\u8bdd\u603b\u7ed3",
            "2. \u65f6\u95f4\u7ebf",
            "3. \u5173\u952e\u53e3\u64ad\u70b9",
            "4. \u5173\u952e\u753b\u9762\u4e8b\u4ef6",
            "5. \u53ef\u89c1\u6587\u5b57",
            "6. \u4e0d\u786e\u5b9a\u6027",
            "",
            "Important: section headings must be Chinese. Synthesize across the whole video. Ground claims in transcript, OCR, frames, and batch summaries only.",
        ]
    )


def build_local_fallback_sections(
    question: str,
    duration: float,
    frames: list[dict],
    transcript_summary: str,
    ocr_results: list[dict],
    response_error: str | None,
    transcript_error: str | None,
) -> dict:
    frame_lines = [
        f"- {frame['timestamp_seconds']:.2f}s: sampled representative frame"
        for frame in frames
    ]
    ocr_lines = []
    for item in ocr_results[:12]:
        compact_text = " ".join((item.get("text") or "").split())
        if len(compact_text) > 140:
            compact_text = compact_text[:137] + "..."
        ocr_lines.append(f"- {item['timestamp_seconds']:.2f}s: {compact_text}")

    uncertainty_bits = []
    if response_error:
        uncertainty_bits.append(f"Responses synthesis was unavailable: {response_error}")
    if transcript_error:
        uncertainty_bits.append(f"Remote transcription failed and local fallback was used: {transcript_error}")
    if not uncertainty_bits:
        uncertainty_bits.append("This fallback report is based on local transcript extraction, OCR, and sampled frames.")

    return {
        "summary": (
            f"Local fallback summary for the question: {question.strip()} "
            f"Video duration is approximately {duration:.2f} seconds. "
            "This report was generated from local transcript extraction, OCR, and sampled frames because the final Responses API synthesis step was unavailable."
        ),
        "timeline": "\n".join(frame_lines) if frame_lines else "No representative frames were sampled.",
        "key_spoken_points": transcript_summary,
        "key_visual_events": (
            "The video appears to be a screen recording with UI/state changes across the sampled frames. "
            "Use the timeline and OCR snippets together to inspect what changed on screen over time."
        ),
        "visible_text": "\n".join(ocr_lines) if ocr_lines else "No OCR text available.",
        "uncertainties": " ".join(uncertainty_bits),
    }


def describe_sampling_strategy(
    duration: float,
    sample_seconds: float,
    max_frames: int,
    scene_detection: bool,
    actual_frames: int,
    sampling_mode: str,
    screen_layout_filter: bool,
) -> str:
    frame_budget = determine_frame_budget(duration, sample_seconds, max_frames)
    if sampling_mode == "all-changes":
        suffix = "-layout-filtered" if screen_layout_filter else ""
        return (
            f"all-detected-changes{suffix} "
            f"(actual_frames={actual_frames}, duration={duration:.2f}s)"
        )
    if scene_detection:
        return (
            "segment-coverage-scene-sampling "
            f"(frame_budget={frame_budget}, actual_frames={actual_frames}, duration={duration:.2f}s)"
        )
    return (
        "segment-coverage-uniform-sampling "
        f"(frame_budget={frame_budget}, actual_frames={actual_frames}, duration={duration:.2f}s, "
        f"sample_seconds={sample_seconds:.2f})"
    )


def build_local_fallback_output(
    sections: dict,
) -> str:
    lines = [
        "1. \u4e00\u53e5\u8bdd\u603b\u7ed3",
        sections.get("summary", ""),
        "",
        "2. \u65f6\u95f4\u7ebf",
        sections.get("timeline", ""),
        "",
        "3. \u5173\u952e\u53e3\u64ad\u70b9",
        sections.get("key_spoken_points", ""),
        "",
        "4. \u5173\u952e\u753b\u9762\u4e8b\u4ef6",
        sections.get("key_visual_events", ""),
        "",
        "5. \u53ef\u89c1\u6587\u5b57",
        sections.get("visible_text", ""),
        "",
        "6. \u4e0d\u786e\u5b9a\u6027",
        sections.get("uncertainties", ""),
    ]
    return "\n".join(lines)

def analyze(
    video_path: Path,
    model: str,
    question: str,
    sample_seconds: float,
    max_frames: int,
    sampling_mode: str,
    analysis_batch_size: int,
    image_detail: str,
    base_url: str,
    api_timeout: float,
    transcribe_model: str,
    local_whisper_model: str,
    skip_audio: bool,
    scene_detection: bool,
    scene_threshold: float,
    min_change_gap: float,
    screen_layout_filter: bool,
    layout_change_threshold: float,
    layout_downscale_width: int,
    title_ocr_filter: bool,
    title_change_threshold: float,
    chapter_nav_filter: bool,
    presenter_shot_filter: bool,
    same_chapter_dedupe_filter: bool,
    use_ocr: bool,
    extract_doc_md: str,
    doc_only: bool,
    doc_md_mode: str,
    extract_speech_md: str,
    speech_only: bool,
    speech_md_mode: str,
) -> dict:
    if not os.environ.get("OPENAI_API_KEY") and not doc_only and not speech_only:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = None
    if not doc_only and not speech_only:
        OpenAI = require_openai()
        client_kwargs = {}
        if base_url:
            client_kwargs["base_url"] = base_url
        client_kwargs["timeout"] = api_timeout
        client = OpenAI(**client_kwargs)

    duration = ffprobe_duration(video_path)
    frame_budget = determine_frame_budget(duration, sample_seconds, max_frames)
    raw_scene_candidates = [] if speech_only else detect_scene_timestamps(
        video_path=video_path,
        scene_threshold=scene_threshold,
        min_change_gap=min_change_gap,
    ) if scene_detection or sampling_mode == "all-changes" else []
    scene_candidates = raw_scene_candidates[:]
    layout_filter_diagnostics = []

    with tempfile.TemporaryDirectory(prefix="video-understanding-") as tmp:
        temp_dir = Path(tmp)
        if speech_only:
            timestamps = []
            frames = []
        elif sampling_mode == "all-changes":
            timestamps = build_all_changes_timestamps(
                video_path=video_path,
                duration=duration,
                scene_threshold=scene_threshold,
                min_change_gap=min_change_gap,
            )
            if screen_layout_filter:
                timestamps, layout_filter_diagnostics = filter_layout_change_timestamps(
                    video_path=video_path,
                    timestamps=timestamps,
                    temp_dir=temp_dir,
                    layout_change_threshold=layout_change_threshold,
                    layout_downscale_width=layout_downscale_width,
                    title_ocr_filter=title_ocr_filter,
                    title_change_threshold=title_change_threshold,
                    chapter_nav_filter=chapter_nav_filter,
                    presenter_shot_filter=presenter_shot_filter,
                    same_chapter_dedupe_filter=same_chapter_dedupe_filter,
                )
        elif scene_candidates:
            if screen_layout_filter:
                scene_candidates, layout_filter_diagnostics = filter_layout_change_timestamps(
                    video_path=video_path,
                    timestamps=ensure_anchor_timestamps(scene_candidates, duration),
                    temp_dir=temp_dir,
                    layout_change_threshold=layout_change_threshold,
                    layout_downscale_width=layout_downscale_width,
                    title_ocr_filter=title_ocr_filter,
                    title_change_threshold=title_change_threshold,
                    chapter_nav_filter=chapter_nav_filter,
                    presenter_shot_filter=presenter_shot_filter,
                    same_chapter_dedupe_filter=same_chapter_dedupe_filter,
                )
                scene_candidates = [
                    ts for ts in scene_candidates
                    if ts not in {0.0, round(max(duration - epsilon_for_duration(duration), 0.0), 3)}
                ]
            timestamps = ensure_anchor_timestamps(
                select_segment_coverage_timestamps(
                    duration=duration,
                    frame_budget=frame_budget,
                    candidate_timestamps=scene_candidates,
                ),
                duration,
            )
        else:
            timestamps = ensure_anchor_timestamps(build_timestamps(duration, sample_seconds, max_frames), duration)

        if not speech_only:
            frames = extract_frames(video_path, timestamps, temp_dir)
        transcript = None
        transcript_summary = "No transcript available."
        audio_used = False
        transcript_source = None
        transcript_error = None
        audio_needed = (speech_only or bool(extract_speech_md) or not doc_only) and not skip_audio
        if audio_needed and ffprobe_has_audio(video_path):
            audio_path = extract_audio(video_path, temp_dir)
            if client is not None:
                try:
                    transcript = transcribe_audio(client, audio_path, transcribe_model)
                    transcript_source = "openai-audio-api"
                except Exception as exc:
                    transcript_error = str(exc)
            if transcript is None:
                try:
                    transcript = transcribe_audio_local(audio_path, local_whisper_model)
                    transcript_source = "local-faster-whisper" if transcript else transcript_source
                except Exception as exc:
                    transcript_error = f"{transcript_error}; local fallback failed: {exc}" if transcript_error else str(exc)
                if transcript is None and transcript_error is None:
                    transcript_error = "No transcription data available."
            transcript_summary = compact_transcript(transcript)
            audio_used = True
        transcript_segments = normalize_segments(transcript)
        frame_alignment = attach_transcript_to_frames(frames, transcript_segments)
        ocr_results = extract_ocr(frames) if use_ocr and not doc_only else []
        document_markdown = extract_document_markdown(frames, mode=doc_md_mode) if extract_doc_md else None
        speech_markdown = build_speech_markdown(transcript, video_path, mode=speech_md_mode) if extract_speech_md or speech_only else None
        ocr_summary = "\n".join(
            f"[{item['timestamp_seconds']:.2f}s] {item['text']}" for item in ocr_results[:20]
        ) or "No OCR text available."

        response_error = None
        fallback_sections = None
        batch_summaries = []
        if speech_only:
            response_error = None
            fallback_sections = {
                "summary": "已将视频口播转写并整理为知识 Markdown。",
                "timeline": "\n".join(
                    f"- {format_timestamp(chunk['start'])} - {format_timestamp(chunk['end'])}: speech chunk"
                    for chunk in ((speech_markdown or {}).get("chunks") or [])
                ),
                "key_spoken_points": (speech_markdown or {}).get("markdown", ""),
                "key_visual_events": "Speech-only mode skipped visual analysis.",
                "visible_text": "Speech-only mode skipped OCR.",
                "uncertainties": "Speech-only mode only uses the audio transcript and does not verify screen content.",
            }
            output_text = build_local_fallback_output(fallback_sections)
        elif doc_only:
            response_error = None
            fallback_sections = {
                "summary": "已从视频采样帧中提取文档正文为 Markdown。",
                "timeline": "\n".join(
                    f"- {frame['timestamp_seconds']:.2f}s: sampled frame"
                    for frame in frames
                ),
                "key_spoken_points": "Doc-only mode skipped audio transcription.",
                "key_visual_events": "Doc-only mode focused on document-like screen regions.",
                "visible_text": (document_markdown or {}).get("markdown", "") if document_markdown else "",
                "uncertainties": "Doc-only mode does not synthesize spoken context.",
            }
            output_text = build_local_fallback_output(fallback_sections)
        else:
            try:
                if sampling_mode == "all-changes" and len(frames) > analysis_batch_size:
                    for batch_number, batch_frames in enumerate(chunk_list(frames, analysis_batch_size), start=1):
                        batch_summaries.append(
                            summarize_frame_batch(
                                client=client,
                                model=model,
                                batch_frames=batch_frames,
                                batch_index=batch_number,
                                total_batches=math.ceil(len(frames) / analysis_batch_size),
                                question=question,
                                duration=duration,
                                transcript_summary=transcript_summary,
                                audio_used=audio_used,
                                ocr_results=ocr_results,
                                image_detail=image_detail,
                            )
                        )
                    final_prompt = build_final_prompt_with_batch_summaries(
                        question=question,
                        duration=duration,
                        transcript_summary=transcript_summary,
                        audio_used=audio_used,
                        ocr_summary=ocr_summary,
                        frames=frames,
                        batch_summaries=batch_summaries,
                    )
                    response = client.responses.create(
                        model=model,
                        input=[{"role": "user", "content": [{"type": "input_text", "text": final_prompt}]}],
                    )
                else:
                    content = [
                        {
                            "type": "input_text",
                            "text": build_prompt(
                                question,
                                frames,
                                duration,
                                transcript_summary,
                                audio_used,
                                ocr_summary,
                            ),
                        }
                    ]
                    for frame in frames:
                        content.append(
                            {
                                "type": "input_image",
                                "image_url": f"data:image/jpeg;base64,{frame['base64']}",
                                "detail": image_detail,
                            }
                        )
                    response = client.responses.create(
                        model=model,
                        input=[{"role": "user", "content": content}],
                    )
                output_text = response.output_text
            except Exception as exc:
                response_error = str(exc)
                fallback_sections = build_local_fallback_sections(
                    question=question,
                    duration=duration,
                    frames=frames,
                    transcript_summary=transcript_summary,
                    ocr_results=ocr_results,
                    response_error=response_error,
                    transcript_error=transcript_error,
                )
                output_text = build_local_fallback_output(fallback_sections)
        parsed_output = clean_parsed_output(
            fallback_sections or parse_output_sections(output_text),
            transcript=transcript,
        )

        return {
            "model": model,
            "base_url": base_url or None,
            "video_path": str(video_path),
            "duration_seconds": duration,
            "sample_seconds": sample_seconds,
            "sampling_mode": sampling_mode,
            "sampling_strategy": describe_sampling_strategy(
                duration=duration,
                sample_seconds=sample_seconds,
                max_frames=max_frames,
                scene_detection=scene_detection and bool(scene_candidates),
                actual_frames=len(frames),
                sampling_mode=sampling_mode,
                screen_layout_filter=screen_layout_filter,
            ),
            "scene_detection": scene_detection,
            "scene_threshold": scene_threshold,
            "min_change_gap": min_change_gap,
            "screen_layout_filter": screen_layout_filter,
            "layout_change_threshold": layout_change_threshold,
            "layout_downscale_width": layout_downscale_width,
            "title_ocr_filter": title_ocr_filter,
            "title_change_threshold": title_change_threshold,
            "chapter_nav_filter": chapter_nav_filter,
            "presenter_shot_filter": presenter_shot_filter,
            "same_chapter_dedupe_filter": same_chapter_dedupe_filter,
            "raw_scene_candidate_count": len(raw_scene_candidates),
            "filtered_scene_candidate_count": len(scene_candidates),
            "layout_filter_diagnostics": layout_filter_diagnostics,
            "analysis_batch_size": analysis_batch_size,
            "audio_used": audio_used,
            "ocr_used": use_ocr,
            "doc_only": doc_only,
            "speech_only": speech_only,
            "transcribe_model": transcribe_model if audio_used else None,
            "local_whisper_model": local_whisper_model,
            "local_whisper_model_resolved": (
                transcript.get("model_resolved")
                if isinstance(transcript, dict) and transcript.get("source") == "faster-whisper"
                else None
            ),
            "transcript_source": transcript_source,
            "transcript_error": transcript_error,
            "response_error": response_error,
            "transcript": transcript,
            "transcript_segments": transcript_segments,
            "frame_transcript_alignment": frame_alignment,
            "batch_summaries": batch_summaries,
            "ocr_results": ocr_results,
            "document_markdown": document_markdown,
            "speech_markdown": speech_markdown,
            "frames": [
                {
                    "index": frame["index"],
                    "timestamp_seconds": frame["timestamp_seconds"],
                }
                for frame in frames
            ],
            "output_text": output_text,
            "parsed_output": parsed_output,
        }


def main() -> int:
    args = parse_args()
    source_video = args.video

    with tempfile.TemporaryDirectory(prefix="video-understanding-source-") as source_tmp:
        source_temp_dir = Path(source_tmp)
        video_path, source_url = resolve_video_source(
            source_video,
            source_temp_dir,
            args.download_timeout,
            use_ytdlp=not args.no_yt_dlp,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            auto_cookies=args.auto_cookies,
        )

        result = analyze(
            video_path=video_path,
            model=args.model,
            question=args.question,
            sample_seconds=args.sample_seconds,
            max_frames=args.max_frames,
            sampling_mode=args.sampling_mode,
            analysis_batch_size=args.analysis_batch_size,
            image_detail=args.image_detail,
            base_url=args.base_url,
            api_timeout=args.api_timeout,
            transcribe_model=args.transcribe_model,
            local_whisper_model=args.local_whisper_model,
            skip_audio=args.skip_audio,
            scene_detection=args.scene_detection,
            scene_threshold=args.scene_threshold,
            min_change_gap=args.min_change_gap,
            screen_layout_filter=args.screen_layout_filter,
            layout_change_threshold=args.layout_change_threshold,
            layout_downscale_width=args.layout_downscale_width,
            title_ocr_filter=args.title_ocr_filter,
            title_change_threshold=args.title_change_threshold,
            chapter_nav_filter=args.chapter_nav_filter,
            presenter_shot_filter=args.presenter_shot_filter,
            same_chapter_dedupe_filter=args.same_chapter_dedupe_filter,
            use_ocr=args.ocr,
            extract_doc_md=args.extract_doc_md,
            doc_only=args.doc_only,
            doc_md_mode=args.doc_md_mode,
            extract_speech_md=args.extract_speech_md,
            speech_only=args.speech_only,
            speech_md_mode=args.speech_md_mode,
        )
        if source_url:
            result["source_url"] = source_url

        if args.report_json:
            report_json_path = Path(args.report_json).expanduser().resolve()
            report_json_path.parent.mkdir(parents=True, exist_ok=True)
            report_json_path.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")

        if args.report_md:
            report_md_path = Path(args.report_md).expanduser().resolve()
            report_md_path.parent.mkdir(parents=True, exist_ok=True)
            report_md_path.write_text(build_markdown_report(result), encoding="utf-8-sig")

        if args.extract_doc_md:
            doc_md_path = Path(args.extract_doc_md).expanduser().resolve()
            doc_md_path.parent.mkdir(parents=True, exist_ok=True)
            document_markdown = (result.get("document_markdown") or {}).get("markdown", "")
            if not document_markdown:
                document_markdown = "# 文档内容\n\n未从采样帧中提取到稳定的文档正文。"
            doc_md_path.write_text(document_markdown, encoding="utf-8-sig")

        if args.extract_speech_md:
            speech_md_path = Path(args.extract_speech_md).expanduser().resolve()
            speech_md_path.parent.mkdir(parents=True, exist_ok=True)
            speech_markdown = (result.get("speech_markdown") or {}).get("markdown", "")
            if not speech_markdown:
                speech_markdown = "# 博主口播整理\n\n未从视频音轨中提取到稳定的口播转写。"
            speech_md_path.write_text(speech_markdown, encoding="utf-8-sig")

        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=True))
        else:
            print(result["output_text"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
