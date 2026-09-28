"""LLM access with bounded retries and deadlines.

A request is retried (with jittered backoff) only if the provider fails before the
first token, so a partially streamed answer is never duplicated. When the retry
budget or deadline is exhausted the caller gets LLMUnavailable and must return a
clear error instead of an answer.
"""

import asyncio
from typing import AsyncIterator, Optional

from backend.common.metrics import metrics
from backend.common.retry import backoff_delay
from backend.config import (
    GROQ_API_KEY, GROQ_MODEL, LLM_FIRST_TOKEN_TIMEOUT, LLM_MAX_ATTEMPTS, LLM_MAX_TOKENS,
    LLM_PROVIDER, LLM_TEMPERATURE, LLM_TOTAL_TIMEOUT, groq_model_kwargs,
)


class LLMUnavailable(RuntimeError):
    pass


class LLMClient:
    provider = LLM_PROVIDER
    model = GROQ_MODEL

    def __init__(self, api_key: Optional[str] = None):
        from langchain_groq import ChatGroq  # lazy: keeps cold starts short

        key = api_key or GROQ_API_KEY
        self.llm = ChatGroq(
            model=GROQ_MODEL, api_key=key, temperature=LLM_TEMPERATURE, max_tokens=LLM_MAX_TOKENS,
            **groq_model_kwargs(),
        ) if key else None

    @property
    def available(self) -> bool:
        return self.llm is not None

    async def stream(self, system_prompt: str, user_message: str) -> AsyncIterator[str]:
        if not self.llm:
            raise LLMUnavailable("No LLM provider is configured")

        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LLM_TOTAL_TIMEOUT

        for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
            emitted = False
            agen = self.llm.astream(messages).__aiter__()
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
            except LLMUnavailable:
                metrics.incr("llm.errors")
                raise
            except Exception as e:  # provider error or first-token timeout
                metrics.incr("llm.errors")
                delay = backoff_delay(attempt, base=0.5, cap=4)
                if emitted or attempt == LLM_MAX_ATTEMPTS or loop.time() + delay >= deadline:
                    raise LLMUnavailable(f"LLM provider error: {type(e).__name__}") from e
                metrics.incr("llm.retries")
                await asyncio.sleep(delay)
            finally:
                aclose = getattr(agen, "aclose", None)
                if aclose:
                    try:
                        await aclose()
                    except Exception:
                        pass

    async def complete(self, system_prompt: str, user_message: str) -> str:
        parts = [token async for token in self.stream(system_prompt, user_message)]
        return "".join(parts)
