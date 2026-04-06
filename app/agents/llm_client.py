"""Shared Claude API client for all agents."""
import logging

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


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

    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = response.content[0].text
    logger.info(f"Claude response: {len(text)} chars, tokens: {response.usage.input_tokens}+{response.usage.output_tokens}")
    return text


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
