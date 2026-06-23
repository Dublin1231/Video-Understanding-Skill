# OpenAI Hybrid Path

Use this path when you have:

- `OPENAI_API_KEY`
- a Python runtime
- `ffmpeg`

## Why this is the default

Current public OpenAI docs clearly support:

- image inputs through the Responses API
- audio/speech workflows
- file inputs for specific document classes

For general-purpose video understanding, the most dependable implementation is still hybrid:

1. extract sparse frames locally
2. optionally extract audio/transcript locally
3. send frames plus instructions to the model
4. ask for a time-aligned result

## Strengths

- works with current public multimodal primitives
- easy to audit
- controllable sampling rate
- compatible with tutorial videos, UI recordings, lectures, and demos

## Weaknesses

- depends on local extraction
- may miss very brief events if sampling is too sparse
- spoken content still benefits from explicit transcription

## Recommended settings

- start with `gpt-5.5`
- use sparse frames for overview
- increase frame density only if the first pass misses temporal detail
- ask for timestamps or interval indices in the response

