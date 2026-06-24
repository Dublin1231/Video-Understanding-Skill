---
name: "video-understanding"
description: "Use when Codex needs to understand what a video says and what happens on screen over time from local files such as .mp4, .mov, .mkv, .webm, video URLs, webpage video links supported by yt-dlp, or a folder of extracted clips/frames. Trigger this skill when the user sends a video file path, a video URL, or asks to transcribe, summarize, OCR, extract documents from, or convert a video into knowledge Markdown."
---

# Video Understanding

Turn video understanding into a deterministic workflow instead of ad hoc frame guessing.

This skill does not magically add a brand-new video modality to Codex. It gives Codex a reliable way to decide which path is available in the current environment, gather the right artifacts, and produce a time-aligned explanation of what is said and shown.

In current public OpenAI docs, the stable building blocks are image inputs, audio/speech workflows, and file inputs. For general video understanding, the most reliable approach is usually a hybrid path: extract sparse frames plus audio locally, then send those artifacts to the OpenAI API for multimodal analysis.

## Triggering

Use this skill automatically when the user provides:

- a local video path ending in `.mp4`, `.mov`, `.mkv`, `.webm`, `.m4v`, or `.avi`
- a direct downloadable video URL ending in a common video extension
- a webpage video link that can be downloaded by `yt-dlp`
- a request such as "analyze this video", "summarize this video", "turn this video into notes", "extract the document shown in this video", or "transcribe the speaker"

Video URLs can be passed to `scripts/analyze_video_with_openai.py` directly. The script first tries direct media download; if the URL is a webpage, it falls back to `yt-dlp` when installed. Site support depends on `yt-dlp`, network access, cookies/login status, DRM, and the user's permission to download the video.

For sites that require login or fresh cookies, prefer a Netscape-format `--cookies <cookies.txt>` file. `--cookies-from-browser <browser>` and `--cookies-from-browser auto` are convenience fallbacks, but on newer Windows Chrome/Edge versions they may fail with DPAPI / App-Bound Encryption errors. If that happens, ask the user to export `cookies.txt`, try Firefox login state, or provide a local downloaded video.

## Choose The Right Mode

Tell the user these choices when their goal is unclear:

| User goal | Recommended mode |
| --- | --- |
| Understand what is said and shown | full video report with `--ocr` and optional `--report-md` |
| Turn speaker voice into knowledge notes | `--speech-only --speech-md-mode knowledge --extract-speech-md <path>` |
| Get a timestamped transcript | `--speech-only --speech-md-mode literal --extract-speech-md <path>` |
| Extract an on-screen document or article | `--doc-only --doc-md-mode literal --extract-doc-md <path>` |
| Capture every screen/page change | `--sampling-mode all-changes --scene-detection --screen-layout-filter` |
| Analyze a screen recording with chapters | add `--title-ocr-filter --chapter-nav-filter --same-chapter-dedupe-filter` |
| Webpage video cannot be downloaded | record browser playback with `scripts/record_webpage_playback.py`, then analyze the captured mp4 |

## Quick Start

1. Run the capability probe first.

```powershell
& 'C:\Users\35647\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  "$env:USERPROFILE\.codex\skills\video-understanding\scripts\capability_probe.py"
```

2. Choose the first viable path in this order:
   - Hybrid OpenAI path via `scripts/analyze_video_with_openai.py`
   - Native multimodal video input, if the current model/tooling can directly accept the video.
   - Audio + visual pipeline, if direct video input is unavailable.
   - User-assisted fallback, if required binaries or APIs are missing.

3. Produce a time-aligned output, not a generic paragraph.
   - Include what is spoken.
   - Include what changes visually.
   - Include OCR text that matters.
   - Note uncertainty explicitly.

## Workflow

### 1. Resolve the video source

Accept local files, direct media URLs, and webpage video links. For URLs, the script first attempts direct media download, then falls back to `yt-dlp` when the URL is a webpage and `yt-dlp` is installed.

If a webpage cannot be downloaded because of login, permissions, DRM, or site restrictions, explain the blocker and try the browser playback recording fallback when appropriate. This fallback records what is visible on the desktop into an mp4, then feeds that mp4 into the normal analysis pipeline. It can reliably capture visuals; audio capture requires an available system loopback or virtual audio device.

### 2. Inspect the environment

Run `scripts/capability_probe.py` before choosing an approach. It reports:

- available runtimes
- `OPENAI_API_KEY` presence
- `ffmpeg` availability
- whether common Python modules are installed
- whether a direct OpenAI API path is likely possible

Do not assume local media tooling exists.

### 3. Prefer the hybrid OpenAI API path when available

If `OPENAI_API_KEY` is set and local extraction dependencies exist, use:

```powershell
& 'C:\Users\35647\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  "$env:USERPROFILE\.codex\skills\video-understanding\scripts\analyze_video_with_openai.py" `
  "C:\path\to\video.mp4" --question "这视频讲了什么，画面里发生了什么？"
```

This path:

- extracts sparse frames with `ffmpeg`
- extracts audio when present
- transcribes speech when possible
- falls back to a local `faster-whisper` model if the remote transcription gateway is broken or empty
- covers long videos by splitting the full duration into time windows and sampling across the whole video, not only the opening minutes
- sends frames plus transcript evidence to the Responses API
- asks for a time-aligned summary
- can emit structured JSON and Markdown reports for downstream workflows

Read [references/openai-hybrid-path.md](references/openai-hybrid-path.md) when using this path.

### 4. Prefer direct multimodal analysis when actually available

If the current environment can send video or prebuilt multimodal inputs to the model, prefer that path for the first pass. Use it when the user wants:

- a semantic summary of the whole video
- event ordering
- speaker intent
- coarse scene understanding

Even on the direct path, still request a structured answer with timestamps or time ranges.

Read [references/native-openai-path.md](references/native-openai-path.md) when using this path.

### 5. Use the timeline pipeline when direct video input is unavailable

If direct video input is not available, build a time-aligned artifact set instead of relying on one screenshot.

Minimum pipeline:

1. Extract or obtain transcript.
2. Detect coarse visual segments or chapters.
3. Sample representative visuals per segment, not one global frame.
4. Extract OCR where relevant.
5. Merge transcript + visuals into a single timeline summary.

This is the default path for most local Codex environments.

Read [references/timeline-pipeline.md](references/timeline-pipeline.md) when using this path.

### 6. Match the output to the request

Possible deliverables:

- short summary
- scene-by-scene outline
- transcript with visual annotations
- moderation/review notes
- tutorial or lecture notes
- product demo walkthrough

When the user asks "what is this video about", prefer:

- 3-6 bullet high-level summary
- ordered timeline of major moments
- unresolved uncertainties

When the user asks "what appears on screen", prefer:

- scene list with timestamps
- OCR snippets
- object/action changes
- UI state transitions if it is a software recording

## Prompting Rules

Always ask for structured output. Prefer this shape:

```text
1. One-paragraph summary
2. Timeline
3. Key spoken points
4. Key visual events
5. On-screen text / OCR
6. Uncertainties
```

If the source is instructional or technical, ask for:

- goals of the speaker
- steps being demonstrated
- tools or UI elements shown
- prerequisites or warnings mentioned

Read [references/prompt-templates.md](references/prompt-templates.md) for reusable prompts.

## Scripts

### `scripts/capability_probe.py`

Probe the environment and print a machine-readable capability report.

### `scripts/build_analysis_brief.py`

Create a structured analysis brief from a video path, optional transcript path, and optional user question. Use this to standardize what another Codex instance or API call should analyze.

### `scripts/analyze_video_with_openai.py`

Extract representative frames, optionally extract audio, and call the OpenAI Responses API to produce a structured video summary.

### `scripts/record_webpage_playback.py`

Open or record a webpage video from the desktop when direct download and `yt-dlp` fail.

Example:

```powershell
& 'C:\Users\35647\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  "$env:USERPROFILE\.codex\skills\video-understanding\scripts\record_webpage_playback.py" `
  "https://www.douyin.com/video/7623595912924777780" `
  --duration 60 `
  --output "outputs\browser-capture.mp4"
```

Then analyze the captured file:

```powershell
& 'C:\Users\35647\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  "$env:USERPROFILE\.codex\skills\video-understanding\scripts\analyze_video_with_openai.py" `
  "outputs\browser-capture.mp4" --ocr --report-md "outputs\browser-capture-report.md"
```

Use `--list-devices` to inspect possible audio devices. If the machine exposes a stereo mix, loopback, or virtual audio device, pass it with `--audio-device "<device name>"`; otherwise the recording is visual-only.
Use `--auto-audio` to let the script try to pick a system playback/loopback/virtual audio device. It intentionally avoids plain microphones when auto-selecting because microphones usually record room noise, not browser audio. Add `--audio-required` when the task must include audio and should fail instead of silently recording visuals only.

Useful flags:

- `--json` to print the full structured object
- `--report-json <path>` to save a machine-readable report
- `--report-md <path>` to save a human-readable Markdown report
- `--scene-detection` to prefer scene-change sampling
- `--sampling-mode all-changes` to treat every detected page/scene change as a sampling point
- `--screen-layout-filter` to apply a stricter second-pass filter tuned for screen recordings
- `--title-ocr-filter` to add title-region OCR into the page-change decision
- `--chapter-nav-filter` to use a bottom chapter navigation bar as an extra section-change signal
- `--presenter-shot-filter` to suppress standalone talking-head presenter shots unless other section-change signals are also present
- `--same-chapter-dedupe-filter` to conservatively collapse near-duplicate page changes when chapter context is unchanged and only local slide content shifts
- `--ocr` to extract on-screen text from sampled frames
- `--extract-doc-md <path>` to extract document-like text shown in sampled frames into a standalone Markdown file
- `--doc-only` to skip audio transcription and model synthesis when the goal is only document-to-Markdown extraction
- `--doc-md-mode literal|polished` to choose whether the document Markdown preserves on-screen extracted text (`literal`, default) or groups content into generated knowledge headings (`polished`)
- `--extract-speech-md <path>` to transcribe the blogger/speaker audio and write it as standalone Markdown
- `--speech-only` to skip visual sampling, OCR, document extraction, and model synthesis when the goal is only speech-to-knowledge Markdown
- `--speech-md-mode literal|knowledge` to choose whether speech Markdown is timestamped transcript (`literal`) or generated knowledge-note sections with timestamped excerpts (`knowledge`, default)
- `--api-timeout <seconds>` to raise the HTTP timeout for larger videos or slower endpoints
- `--download-timeout <seconds>` to raise the timeout for direct URL or `yt-dlp` downloads
- `--no-yt-dlp` to disable webpage-video download fallback and only accept local files or direct media URLs
- `--cookies <cookies.txt>` to pass a Netscape-format cookie file to `yt-dlp`
- `--cookies-from-browser <browser|auto>` to let `yt-dlp` read cookies from a browser such as `chrome`, `edge`, or `firefox`; `auto` tries common browsers
- `--auto-cookies` to try common local browsers automatically after a normal webpage download fails
- `--local-whisper-model <name-or-path>` to select the local fallback ASR model

Long-video coverage notes:

- Sampling now uses segment coverage across the whole duration by default.
- When `--scene-detection` is enabled, the script still tries to find visual scene changes, but it chooses them per time window so later parts of the video are not starved.
- When `--sampling-mode all-changes` is enabled, the script extracts every detected visual change, forces start/end anchors, and batches analysis across multiple Responses calls if needed.
- When `--screen-layout-filter` is enabled, a second-pass image-difference filter removes many small transitions, subtle animations, and micro-movements that are common in screen recordings.
- When `--title-ocr-filter` is enabled, the script searches for a title-like text block in the upper page area, not just any top-bar text, and applies a conservative title gate: title changes only help on borderline layout changes, and window bars, status bars, and noisy OCR do not count as independent keep signals.
- When `--chapter-nav-filter` is enabled, the script also reads a bottom chapter navigation bar and can use chapter-highlight changes as a conservative assist for borderline section transitions.
- When `--presenter-shot-filter` is enabled, the script can detect centered talking-head presenter shots and avoid keeping them as separate page changes unless stronger section-change evidence is present.
- When `--same-chapter-dedupe-filter` is enabled, the script uses stabilized chapter-nav context plus title/template similarity to drop some same-section near-duplicate pages that would otherwise survive as strong layout changes.
- When `--ocr` is enabled, the script uses local Chinese + English Tesseract data when available, preprocesses screen frames, OCRs multiple screen regions, filters noisy lines, and corrects common tool-name mistakes such as Obsidian, ClaudeCode, and OpenClaw.
- When `--extract-doc-md <path>` is enabled, the script looks for document-reading or document-explaining frames, crops the central document body, suppresses subtitles/presenter overlays/app chrome, OCRs only the document region, merges repeated lines across frames, and writes Markdown.
- Pair `--extract-doc-md <path>` with `--doc-only` when the user only wants the visible document converted into Markdown for a knowledge base.
- Use the default `--doc-md-mode literal` when the user wants text that was actually visible in the video. Use `--doc-md-mode polished` only when the user explicitly wants generated knowledge-base grouping; generated headings must not be presented as original video headings.
- When `--extract-speech-md <path>` is enabled, the script writes a separate Markdown note from the audio transcript. Pair it with `--speech-only` when the user wants only the blogger/speaker's voice converted into knowledge notes.
- Use the default `--speech-md-mode knowledge` when the user wants a usable knowledge-base note. It creates generated sections such as core ideas, process, tools, and cases, then keeps timestamped original speech excerpts for traceability. Use `--speech-md-mode literal` when the user wants the raw transcript arranged by time.
- Speech Markdown generation can run without `OPENAI_API_KEY` in `--speech-only` mode because it uses the local faster-whisper fallback directly.
- Long transcripts are summarized from the start, middle, and end of the video instead of only the first few segments, so late-video speech is not incorrectly treated as missing.
- The report normalizes known video-specific aliases such as `OBZ硬支撑` to `Obsidian 知识库`, `OpenCore` to `OpenClaw`, and `Cloud Code` to `Claude Code` before synthesis and uncertainty cleanup.
- The output JSON includes `sampling_strategy` so you can verify how frames were selected.
- Layout-filter diagnostics now include title quality, title-block-like geometry, chapter-nav state, stabilized chapter context, presenter-shot state, same-chapter duplicate signals, meaningful-title detection, and `keep_reason` so you can see why a candidate frame was kept or dropped.

Local Whisper notes:

- This skill includes a real offline transcription path through `faster-whisper`.
- Downloaded models are stored under `C:\Users\35647\.codex\skills\video-understanding\models`.
- The current default local model is `small`.
- If the remote transcription endpoint returns HTML, an empty transcript, or another upstream failure, the script automatically falls back to the local model.

Local OCR notes:

- This skill includes local Tesseract language data under `C:\Users\35647\.codex\skills\video-understanding\models\tessdata`.
- The script sets `TESSDATA_PREFIX` to that directory when available, so Chinese OCR does not silently degrade to English-only OCR.
- Screen-recording OCR uses region-based extraction and quality filtering because whole-frame OCR is usually noisy for slides, sidebars, and phone screenshots.

## Guardrails

- Do not claim direct video understanding if the environment only supports still images plus transcript.
- Do not summarize from a single frame when the task requires temporal understanding.
- Do not treat ASR transcript as fully accurate; note low-confidence or missing segments.
- Do not omit OCR when the video is UI-heavy, slide-based, or caption-driven.
- Do not hide missing dependencies; say exactly what is unavailable.

## References

- OpenAI hybrid path: `references/openai-hybrid-path.md`
- Native multimodal path: `references/native-openai-path.md`
- Timeline pipeline: `references/timeline-pipeline.md`
- Prompt templates: `references/prompt-templates.md`
