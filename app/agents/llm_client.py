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

    response = await client.messages.create(
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

        response = await client.messages.create(
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
