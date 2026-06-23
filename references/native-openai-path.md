# Native OpenAI Path

Use this reference when the environment can send multimodal inputs to an OpenAI model directly.

For most public API workflows today, direct understanding is usually implemented from video-derived artifacts rather than a single generic "upload video for understanding" API primitive. If the environment exposes only image and audio inputs, fall back to the hybrid path instead of pretending there is native end-to-end video ingestion.

## Goal

Ask the model to understand the video as a time-based artifact, not as an isolated still image.

## Strategy

1. Prefer a direct video input path if the current API/runtime supports it.
2. If direct video file input is not supported in the current runtime, provide:
   - an ordered set of representative images
   - transcript or subtitles
   - the user's exact question
3. Ask for time-aligned output.

## Minimum analysis prompt

```text
Analyze this video and explain both:
1. what is being said
2. what happens visually over time

Return:
- a short summary
- a timeline with timestamps or time ranges
- key spoken claims
- key visual changes
- visible on-screen text
- uncertainties or places where the evidence is weak
```

## Good use cases

- user wants a semantic summary
- user wants lecture/demo notes
- user wants a scene-by-scene explanation
- user wants alignment between speech and visuals

## Weak spots

- long videos may exceed context or upload limits
- low audio quality still hurts results
- fast UI changes still benefit from OCR and segmentation

## Recommendation

Even when using a native multimodal path, keep the timeline mindset. Ask the model to ground claims in time ranges rather than giving one global description.
