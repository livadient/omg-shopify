"""Tests for app/agents/llm_client.py — retry / backoff behavior on transient errors."""
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

from app.agents.llm_client import (
    MAX_RETRIES,
    RETRY_STATUSES,
    _create_with_retry,
)


def _fake_status_error(status: int) -> anthropic.APIStatusError:
    """Build an anthropic.APIStatusError with the given HTTP status code.

    The SDK requires a Response object — we use a minimal httpx.Response.
    """
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=status, request=request, json={
        "type": "error",
        "error": {"type": "overloaded_error" if status == 529 else "api_error", "message": "test"},
    })
    return anthropic.APIStatusError(
        message=f"Test error {status}",
        response=response,
        body={"type": "error", "error": {"type": "api_error", "message": "test"}},
    )


def _fake_connection_error() -> anthropic.APIConnectionError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=request)


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Mock asyncio.sleep so retry tests run instantly instead of waiting backoff."""
    async def _noop(_seconds):
        pass
    monkeypatch.setattr("app.agents.llm_client.asyncio.sleep", _noop)


class TestCreateWithRetry:

    @pytest.mark.asyncio
    async def test_success_first_try_no_retry(self):
        client = MagicMock()
        client.messages.create = AsyncMock(return_value="OK")

        result = await _create_with_retry(client, model="x", max_tokens=10, messages=[])

        assert result == "OK"
        assert client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_overloaded_then_succeeds(self):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=[
            _fake_status_error(529),
            _fake_status_error(529),
            "OK",
        ])

        result = await _create_with_retry(client, model="x", max_tokens=10, messages=[])

        assert result == "OK"
        assert client.messages.create.call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit_429(self):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=[
            _fake_status_error(429),
            "OK",
        ])

        result = await _create_with_retry(client, model="x", max_tokens=10, messages=[])

        assert result == "OK"
        assert client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_server_500(self):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=[
            _fake_status_error(500),
            "OK",
        ])

        result = await _create_with_retry(client, model="x", max_tokens=10, messages=[])
        assert result == "OK"

    @pytest.mark.asyncio
    async def test_retries_on_503(self):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=[
            _fake_status_error(503),
            "OK",
        ])
        result = await _create_with_retry(client, model="x", max_tokens=10, messages=[])
        assert result == "OK"

    @pytest.mark.asyncio
    async def test_retries_on_connection_error(self):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=[
            _fake_connection_error(),
            "OK",
        ])

        result = await _create_with_retry(client, model="x", max_tokens=10, messages=[])

        assert result == "OK"
        assert client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_on_400_bad_request(self):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=_fake_status_error(400))

        with pytest.raises(anthropic.APIStatusError) as exc:
            await _create_with_retry(client, model="x", max_tokens=10, messages=[])

        assert exc.value.status_code == 400
        assert client.messages.create.call_count == 1  # NOT retried

    @pytest.mark.asyncio
    async def test_does_not_retry_on_401_unauthorized(self):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=_fake_status_error(401))

        with pytest.raises(anthropic.APIStatusError):
            await _create_with_retry(client, model="x", max_tokens=10, messages=[])

        assert client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_does_not_retry_on_404(self):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=_fake_status_error(404))

        with pytest.raises(anthropic.APIStatusError):
            await _create_with_retry(client, model="x", max_tokens=10, messages=[])

        assert client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_exhausts_retries_then_raises_last_error(self):
        client = MagicMock()
        # 5 total attempts (1 + MAX_RETRIES), all fail
        client.messages.create = AsyncMock(
            side_effect=[_fake_status_error(529)] * (MAX_RETRIES + 1)
        )

        with pytest.raises(anthropic.APIStatusError) as exc:
            await _create_with_retry(client, model="x", max_tokens=10, messages=[])

        assert exc.value.status_code == 529
        assert client.messages.create.call_count == MAX_RETRIES + 1

    @pytest.mark.asyncio
    async def test_passes_kwargs_through(self):
        client = MagicMock()
        client.messages.create = AsyncMock(return_value="OK")

        await _create_with_retry(
            client,
            model="claude-sonnet-4-20250514",
            max_tokens=42,
            temperature=0.3,
            system="sys",
            messages=[{"role": "user", "content": "hello"}],
        )

        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["max_tokens"] == 42
        assert call_kwargs["temperature"] == 0.3
        assert call_kwargs["system"] == "sys"


class TestRetryStatuses:
    def test_includes_overloaded(self):
        assert 529 in RETRY_STATUSES

    def test_includes_rate_limit(self):
        assert 429 in RETRY_STATUSES

    def test_includes_server_errors(self):
        for code in (500, 502, 503, 504):
            assert code in RETRY_STATUSES, f"missing {code}"

    def test_excludes_client_errors(self):
        for code in (400, 401, 403, 404, 422):
            assert code not in RETRY_STATUSES, f"should not retry {code}"
