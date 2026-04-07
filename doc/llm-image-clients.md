# LLM and Image Clients

## Overview

Shared AI service clients used by all agents. The LLM client wraps the Anthropic Claude API for text generation, and the image client wraps OpenAI's DALL-E 3 for design image generation.

## LLM Client

**File:** `app/agents/llm_client.py`

Wrapper around the Anthropic Claude API. Uses a singleton `AsyncAnthropic` client instance (created on first call).

**Default model:** `claude-sonnet-4-20250514`

### Functions

#### `generate(system_prompt, user_prompt, model, max_tokens, temperature)`

Standard Claude API call. Returns the text content from the first response block.

**Parameters:**
- `system_prompt` -- System message for Claude
- `user_prompt` -- User message content
- `model` -- Model ID (default: `claude-sonnet-4-20250514`)
- `max_tokens` -- Maximum response tokens (default: 4096)
- `temperature` -- Sampling temperature (default: 0.7)

#### `generate_with_search(system_prompt, user_prompt, model, max_tokens, temperature)`

Claude API call with the `web_search_20250305` tool enabled (max 5 uses per call). Handles the tool use loop automatically:

1. Sends initial request with web search tool
2. If Claude uses search (`stop_reason == "tool_use"`), collects text blocks and continues the conversation
3. Handles `server_tool_use` blocks by returning "Search completed." tool results
4. Loops until Claude stops using tools
5. Joins all text blocks from all rounds

#### `generate_json(system_prompt, user_prompt, model, max_tokens, temperature)`

Calls `generate()` and parses the response as JSON. Handles responses wrapped in markdown code blocks:
- Strips `` ```json ... ``` `` wrappers
- Strips `` ``` ... ``` `` wrappers
- Returns parsed `dict`

### Client Lifecycle

```python
_client: AsyncAnthropic | None = None

def _get_client() -> AsyncAnthropic:
    # Creates singleton on first call
    # Uses settings.anthropic_api_key
```

## Image Client

**File:** `app/agents/image_client.py`

DALL-E 3 image generation and background removal for t-shirt designs. Uses a singleton `AsyncOpenAI` client instance.

### `generate_design(concept, style, size, quality)`

Generates a t-shirt design using DALL-E 3.

**Parameters:**
- `concept` -- Design description (e.g., "retro sunset with palm trees")
- `style` -- Art style (default: `"bold graphic illustration"`)
- `size` -- Image dimensions (default: `"1024x1024"`)
- `quality` -- DALL-E quality setting (default: `"hd"`)

**Prompt template:**
```
Create a standalone graphic artwork for printing: {concept}.
Style: {style}.
IMPORTANT: This is ONLY the graphic/artwork/illustration itself --
do NOT show a t-shirt, clothing, mannequin, or any garment.
Just the design artwork on a plain solid white background.
Requirements: solid white background, high contrast, clean sharp edges,
bold and eye-catching artwork suitable for screen printing.
No copyrighted characters or logos. Centered composition.
```

**Returns:** `Path` to the saved PNG file in `static/proposals/`.

File naming: `design_{uuid_hex[:8]}.png` (e.g., `design_a1b2c3d4.png`).

### `remove_background(image_path)`

Removes the background from an image for print-ready transparent PNG.

- Uses the `rembg` library with the `u2net` model
- Saves output as `{original_stem}_nobg.png` alongside the original
- Falls back gracefully if `rembg` or `onnxruntime` is not installed
- Falls back to original image on any processing error

**Returns:** `Path` to the background-removed PNG (or original on failure).

### `generate_text_design(concept, style, size, quality)`

Generates a design using DALL-E 3 with transparent RGBA background (instead of white). Used for designs that contain text/slogans. Pillow-based text designs (slogan type) skip `rembg` since they are already transparent.

### `validate_design_text(image_path, expected_text)`

Uses Claude vision to read text in a generated design image and verify it matches the expected text. Returns a validation result indicating whether the text is correct.

### `generate_design_with_text_check(concept, expected_text, style, size, quality)`

End-to-end pipeline: generates a design via DALL-E 3, validates text with Claude vision, and regenerates with a correction prompt if the text is wrong. Retries up to 2 times before returning the best result.

### Output Directory

All generated images are saved to `static/proposals/` (created automatically).

## Configuration

Both clients require API keys set in environment variables:

| Variable | Used By | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | LLM Client | Anthropic Claude API authentication |
| `OPENAI_API_KEY` | Image Client | OpenAI DALL-E 3 API authentication |
