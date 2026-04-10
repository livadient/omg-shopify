"""Shared Claude API client for all agents."""
import asyncio
import logging
import random

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None

# Transient HTTP statuses worth retrying. 429 = rate limited, 5xx = server errors,
# 529 = Anthropic-specific "overloaded". 4xx other than 429 are client errors and
# will not be retried.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})

# 5 total attempts (1 initial + 4 retries), exponential backoff with jitter:
# ~2s, ~4s, ~8s, ~16s — capped at 30s. Worst-case total ~60s of waiting.
MAX_RETRIES = 4
INITIAL_BACKOFF_S = 2.0
BACKOFF_MULTIPLIER = 2.0
MAX_BACKOFF_S = 30.0
JITTER = 0.4  # ±40%


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def _create_with_retry(client: anthropic.AsyncAnthropic, **kwargs):
    """Call client.messages.create with exponential backoff on transient errors.

    Retries on: 429 (rate limit), 500/502/503/504 (server errors), 529 (overloaded),
    connection errors, and timeouts. Does NOT retry on 4xx client errors except 429.

    Raises the last encountered exception if all retries are exhausted.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await client.messages.create(**kwargs)
        except anthropic.APIConnectionError as e:
            last_error: Exception = e
            reason = "connection_error"
        except anthropic.APITimeoutError as e:
            last_error = e
            reason = "timeout"
        except anthropic.APIStatusError as e:
            if e.status_code not in RETRY_STATUSES:
                # 400/401/403/404/etc — client error, retrying won't help
                raise
            last_error = e
            reason = f"http_{e.status_code}"

        if attempt == MAX_RETRIES:
            logger.error(
                f"Claude API: gave up after {MAX_RETRIES + 1} attempts ({reason}): {last_error}"
            )
            raise last_error

        backoff = min(
            INITIAL_BACKOFF_S * (BACKOFF_MULTIPLIER ** attempt),
            MAX_BACKOFF_S,
        )
        jittered = backoff * (1 + random.uniform(-JITTER, JITTER))
        logger.warning(
            f"Claude API: {reason} (attempt {attempt + 1}/{MAX_RETRIES + 1}), "
            f"retrying in {jittered:.1f}s..."
        )
        await asyncio.sleep(jittered)


async def generate(
    system_prompt: str,
    user_prompt: str,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """Call Claude API and return the text response."""
    client = _get_client()
    logger.info(f"Calling Claude ({model}), prompt length: {len(user_prompt)} chars")

    response = await _create_with_retry(
        client,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = response.content[0].text
    logger.info(f"Claude response: {len(text)} chars, tokens: {response.usage.input_tokens}+{response.usage.output_tokens}")
    return text


async def generate_with_search(
    system_prompt: str,
    user_prompt: str,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """Call Claude API with web search tool enabled and return the text response."""
    client = _get_client()
    logger.info(f"Calling Claude with web search ({model}), prompt length: {len(user_prompt)} chars")

    response = await _create_with_retry(
        client,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
    )

    # Collect all text blocks from the response (may include tool use/results)
    text_parts = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)

    # If the model used search and needs to continue, handle tool use loop
    while response.stop_reason == "tool_use":
        # Build messages with assistant response + tool results
        messages = [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": response.content},
        ]
        # Add tool results for any server-side tool use
        tool_results = []
        for block in response.content:
            if block.type == "server_tool_use":
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Search completed.",
                })
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        response = await _create_with_retry(
            client,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=messages,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        )
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)

    full_text = "\n".join(text_parts)
    logger.info(f"Claude search response: {len(full_text)} chars")
    return full_text


async def generate_json(
    system_prompt: str,
    user_prompt: str,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> dict:
    """Call Claude API and parse the JSON response."""
    import json

    text = await generate(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    # Extract JSON from markdown code blocks if present
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    return json.loads(text.strip())
