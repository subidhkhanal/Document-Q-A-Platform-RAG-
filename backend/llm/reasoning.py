"""LLM access with bounded retries, deadlines and model fallback.

A request is retried (with jittered backoff) only if the provider fails before the
first token, so a partially streamed answer is never duplicated. Rate limits are
per model on Groq, so a 429 moves straight on to the next configured model instead
of waiting. When every option is exhausted the caller gets LLMUnavailable and must
return a clear error instead of an answer.
"""

import asyncio
from typing import AsyncIterator, List, Optional, Tuple

from backend.common.metrics import metrics
from backend.common.retry import backoff_delay
from backend.config import (
    GROQ_API_KEY, GROQ_FALLBACK_MODELS, GROQ_MODEL, LLM_FIRST_TOKEN_TIMEOUT, LLM_MAX_ATTEMPTS, LLM_MAX_TOKENS,
    LLM_PROVIDER, LLM_TEMPERATURE, LLM_TOTAL_TIMEOUT, groq_model_kwargs,
)


class LLMUnavailable(RuntimeError):
    def __init__(self, message: str, rate_limited: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited


def is_rate_limit(error: BaseException) -> bool:
    return "RateLimit" in type(error).__name__ or getattr(error, "status_code", None) == 429


class LLMClient:
    provider = LLM_PROVIDER
    model = GROQ_MODEL

    def __init__(self, api_key: Optional[str] = None):
        from langchain_groq import ChatGroq  # lazy: keeps cold starts short

        key = api_key or GROQ_API_KEY
        models = [GROQ_MODEL] + [m for m in GROQ_FALLBACK_MODELS if m != GROQ_MODEL]
        self.clients: List[Tuple[str, object]] = [
            (m, ChatGroq(model=m, api_key=key, temperature=LLM_TEMPERATURE, max_tokens=LLM_MAX_TOKENS,
                         **groq_model_kwargs(m)))
            for m in models
        ] if key else []
        self.last_model: Optional[str] = None

    @property
    def available(self) -> bool:
        return bool(self.clients)

    async def _stream_once(self, llm, messages, deadline: float, loop) -> AsyncIterator[str]:
        emitted = False
        agen = llm.astream(messages).__aiter__()
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise LLMUnavailable("LLM response exceeded the end-to-end deadline")
                timeout = remaining if emitted else min(LLM_FIRST_TOKEN_TIMEOUT, remaining)
                try:
                    chunk = await asyncio.wait_for(agen.__anext__(), timeout=timeout)
                except StopAsyncIteration:
                    return
                if chunk.content:
                    emitted = True
                    yield chunk.content
        finally:
            aclose = getattr(agen, "aclose", None)
            if aclose:
                try:
                    await aclose()
                except Exception:
                    pass

    async def stream(self, system_prompt: str, user_message: str) -> AsyncIterator[str]:
        if not self.clients:
            raise LLMUnavailable("No LLM provider is configured")

        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LLM_TOTAL_TIMEOUT
        last_error: Optional[BaseException] = None

        for model, llm in self.clients:
            for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
                emitted = False
                try:
                    async for token in self._stream_once(llm, messages, deadline, loop):
                        emitted = True
                        yield token
                    self.last_model = model
                    return
                except LLMUnavailable:
                    metrics.incr("llm.errors")
                    raise
                except Exception as e:  # provider error or first-token timeout
                    metrics.incr("llm.errors")
                    last_error = e
                    if emitted:
                        raise LLMUnavailable(f"LLM provider error: {type(e).__name__}") from e
                    if is_rate_limit(e) or isinstance(e, asyncio.TimeoutError):
                        # Quota used up, or the model is stalled before its first token:
                        # move to the next model now rather than retrying this one.
                        metrics.incr("llm.rate_limited" if is_rate_limit(e) else "llm.first_token_timeout")
                        break
                    delay = backoff_delay(attempt, base=0.5, cap=4)
                    if attempt == LLM_MAX_ATTEMPTS or loop.time() + delay >= deadline:
                        break
                    metrics.incr("llm.retries")
                    await asyncio.sleep(delay)
            else:
                continue
            metrics.incr("llm.model_fallback")

        raise LLMUnavailable(
            f"LLM provider error: {type(last_error).__name__ if last_error else 'unknown'}",
            rate_limited=last_error is not None and is_rate_limit(last_error),
        ) from last_error

    async def complete(self, system_prompt: str, user_message: str) -> str:
        parts = [token async for token in self.stream(system_prompt, user_message)]
        return "".join(parts)
