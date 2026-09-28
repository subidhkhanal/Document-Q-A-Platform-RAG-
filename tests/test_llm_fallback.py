import asyncio

import pytest

from backend.llm.reasoning import LLMClient, LLMUnavailable, is_rate_limit


class RateLimitError(Exception):
    status_code = 429


class _Chunk:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, tokens=None, error=None):
        self.tokens, self.error, self.calls = tokens or [], error, 0

    def astream(self, messages):
        self.calls += 1

        async def gen():
            if self.error:
                raise self.error
            for t in self.tokens:
                yield _Chunk(t)

        return gen()


def _client(*llms):
    client = LLMClient.__new__(LLMClient)
    client.clients = [(f"model-{i}", llm) for i, llm in enumerate(llms)]
    client.last_model = None
    return client


async def _collect(client):
    return "".join([t async for t in client.stream("system", "user")])


def test_rate_limited_model_falls_back_to_next():
    first, second = _FakeLLM(error=RateLimitError()), _FakeLLM(tokens=["Hello", " there"])
    client = _client(first, second)
    assert asyncio.run(_collect(client)) == "Hello there"
    assert client.last_model == "model-1" and first.calls == 1  # no retry sleep on the limited model


def test_all_models_rate_limited_reports_rate_limit():
    client = _client(_FakeLLM(error=RateLimitError()), _FakeLLM(error=RateLimitError()))
    with pytest.raises(LLMUnavailable) as exc:
        asyncio.run(_collect(client))
    assert exc.value.rate_limited


def test_is_rate_limit_detection():
    assert is_rate_limit(RateLimitError()) and not is_rate_limit(ValueError())


def test_stalled_model_falls_back_without_retrying():
    stalled, healthy = _FakeLLM(error=asyncio.TimeoutError()), _FakeLLM(tokens=["ok"])
    client = _client(stalled, healthy)
    assert asyncio.run(_collect(client)) == "ok" and stalled.calls == 1
