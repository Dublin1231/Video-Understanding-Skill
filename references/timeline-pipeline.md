# Timeline Pipeline

Use this reference when direct video input is unavailable or when stronger traceability is needed.

## Pipeline

1. Obtain transcript
2. Segment the video into coarse scenes or intervals
3. Sample representative visuals per interval
4. Extract OCR from intervals where text matters
5. Merge all evidence into one time-aligned summary

## Why this works

Video understanding is temporal. A single frame loses:

- sequence
- causality
- spoken context
- state changes

Sampling per segment preserves enough temporal structure for Codex to reason over the video.

## Segment heuristics

Prefer semantic segments over arbitrary fixed intervals when possible:

- slide change
- camera cut
- major UI transition
- new speaker/topic

If no segmentation tool exists, use fixed intervals as a fallback:

- short videos: every 5-10 seconds
- lectures/demos: every 10-20 seconds
- gameplay or dense action: every 2-5 seconds

## OCR is required when

- the video shows software UI
- slides contain key facts
- subtitles are burned in
- charts, labels, or code appear on screen

## Recommended deliverable

For each interval, collect:

- `time_range`
- `spoken_summary`
- `visual_summary`
- `ocr_text`
- `confidence`

Then produce:

1. high-level summary
2. ordered timeline
3. unresolved ambiguities

