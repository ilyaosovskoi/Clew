#!/usr/bin/env python3
"""Unit tests for request serialization queues (Issue #9)."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from clew.request_queue import (
    CooldownError,
    QueueConfig,
    QueueFullError,
    QueueRegistry,
    RequestQueue,
    get_queue_registry,
    looks_like_rate_limit,
    unwrap_provider,
    wrap_provider,
)


# ── Rate-limit detection ──────────────────────────────────────────────────


def test_looks_like_rate_limit_matches_common_phrasings():
    assert looks_like_rate_limit(Exception("Rate limit exceeded"))
    assert looks_like_rate_limit(Exception("rate_limit"))
    assert looks_like_rate_limit(Exception("too many requests"))
    assert looks_like_rate_limit(Exception("HTTP 429"))
    assert looks_like_rate_limit(Exception("quota exceeded"))
    assert looks_like_rate_limit(Exception("throttled"))


def test_looks_like_rate_limit_ignores_other_errors():
    assert not looks_like_rate_limit(Exception("auth failed"))
    assert not looks_like_rate_limit(Exception("connection refused"))


# ── Queue config defaults ─────────────────────────────────────────────────


def test_queue_config_defaults():
    c = QueueConfig()
    assert c.max_concurrency == 1
    assert c.max_queue_size == 64
    assert c.cooldown_secs == 5.0
    assert c.max_retries == 3


# ── Sync submit ───────────────────────────────────────────────────────────


def test_submit_sync_runs_callable():
    q = RequestQueue(QueueConfig(max_concurrency=1), name="t")
    result = q.submit_sync(lambda x: x * 2, 21)
    assert result == 42
    assert q.stats()["completed"] == 1


def test_submit_sync_serializes_concurrent_calls():
    """With max_concurrency=1, two concurrent calls must NOT overlap."""
    q = RequestQueue(QueueConfig(max_concurrency=1), name="t")
    log: List[str] = []
    lock = threading.Lock()

    def slow_call(tag: str):
        with lock:
            log.append(f"start {tag}")
        time.sleep(0.05)
        with lock:
            log.append(f"end {tag}")
        return tag

    threads = [
        threading.Thread(target=q.submit_sync, args=(slow_call, "A")),
        threading.Thread(target=q.submit_sync, args=(slow_call, "B")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The "end X" of one call must come before the "start Y" of the other.
    assert log.index("end A") < log.index("start B") or log.index("end B") < log.index("start A")
    assert q.stats()["completed"] == 2


def test_submit_sync_retries_on_rate_limit():
    """A rate-limit error triggers a retry; success on 2nd attempt."""
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("rate limit exceeded")
        return "ok"

    cfg = QueueConfig(
        max_concurrency=1,
        max_retries=2,
        retry_backoff_secs=0.01,
        cooldown_secs=0.01,
    )
    q = RequestQueue(cfg, name="t")
    result = q.submit_sync(flaky)
    assert result == "ok"
    assert attempts["n"] == 2
    assert q.stats()["retried"] == 1
    assert q.stats()["rate_limited"] == 1


def test_submit_sync_propagates_non_rate_limit_errors():
    def boom():
        raise RuntimeError("auth failed")

    q = RequestQueue(QueueConfig(max_concurrency=1), name="t")
    with pytest.raises(RuntimeError, match="auth failed"):
        q.submit_sync(boom)
    assert q.stats()["errors"] == 1


def test_submit_sync_gives_up_after_max_retries():
    attempts = {"n": 0}

    def always_429():
        attempts["n"] += 1
        raise RuntimeError("HTTP 429 too many requests")

    cfg = QueueConfig(
        max_concurrency=1,
        max_retries=2,
        retry_backoff_secs=0.01,
        cooldown_secs=0.01,
    )
    q = RequestQueue(cfg, name="t")
    with pytest.raises(RuntimeError, match="429"):
        q.submit_sync(always_429)
    assert attempts["n"] == 3  # initial + 2 retries


def test_submit_sync_raises_queue_full():
    """If max_queue_size is hit, submit_sync raises QueueFullError."""
    cfg = QueueConfig(max_concurrency=1, max_queue_size=2)
    q = RequestQueue(cfg, name="t")

    # Hold the single in-flight slot from a separate thread so that
    # subsequent submit_sync calls queue up (pending) instead of
    # running immediately.
    release = threading.Event()

    def blocking_call():
        release.wait(timeout=2.0)

    in_flight = threading.Thread(target=q.submit_sync, args=(blocking_call,))
    in_flight.start()
    # Give the in-flight call time to acquire the semaphore.
    time.sleep(0.1)

    # Fill the two pending slots. Each thread swallows QueueFullError
    # to avoid pytest's "unhandled thread exception" warning when the
    # queue races ahead of us.
    def quick_pending():
        return "ok"

    pending_threads = []
    for _ in range(2):
        def _runner():
            try:
                q.submit_sync(quick_pending)
            except QueueFullError:
                pass
        t = threading.Thread(target=_runner)
        t.start()
        pending_threads.append(t)
    # Give the pending threads time to call _acquire_pending_slot.
    time.sleep(0.1)

    # Now pending = 2 (max). The next call must raise QueueFullError.
    with pytest.raises(QueueFullError):
        q.submit_sync(lambda: "fourth")

    release.set()
    in_flight.join(timeout=2.0)
    for t in pending_threads:
        t.join(timeout=2.0)


# ── Async submit ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_async_runs_coroutine():
    q = RequestQueue(QueueConfig(max_concurrency=1), name="t")

    async def coro(x):
        return x * 3

    result = await q.submit_async(coro, 14)
    assert result == 42
    assert q.stats()["completed"] == 1


@pytest.mark.asyncio
async def test_submit_async_retries_on_rate_limit():
    attempts = {"n": 0}

    async def coro():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("rate_limit")
        return "ok"

    cfg = QueueConfig(
        max_concurrency=1,
        max_retries=2,
        retry_backoff_secs=0.01,
        cooldown_secs=0.01,
    )
    q = RequestQueue(cfg, name="t")
    result = await q.submit_async(coro)
    assert result == "ok"
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_submit_async_propagates_non_rate_limit_errors():
    async def boom():
        raise ValueError("network down")

    q = RequestQueue(QueueConfig(max_concurrency=1), name="t")
    with pytest.raises(ValueError, match="network down"):
        await q.submit_async(boom)


# ── Registry ──────────────────────────────────────────────────────────────


def test_registry_returns_same_queue_per_provider():
    reg = QueueRegistry()
    q1 = reg.get_or_create("ollama")
    q2 = reg.get_or_create("ollama")
    assert q1 is q2


def test_registry_returns_distinct_queues_per_provider():
    reg = QueueRegistry()
    q1 = reg.get_or_create("ollama")
    q2 = reg.get_or_create("openai")
    assert q1 is not q2


def test_registry_configure_replaces_queue():
    reg = QueueRegistry()
    old = reg.get_or_create("ollama")
    new = reg.configure("ollama", QueueConfig(max_concurrency=4))
    assert new is not old
    assert reg.get_or_create("ollama") is new
    assert new.stats()["max_concurrency"] == 4


def test_registry_stats_includes_all_queues():
    reg = QueueRegistry()
    reg.get_or_create("a")
    reg.get_or_create("b")
    stats = reg.stats()
    assert "a" in stats
    assert "b" in stats


def test_get_queue_registry_singleton():
    a = get_queue_registry()
    b = get_queue_registry()
    assert a is b


# ── Cooldown behaviour ────────────────────────────────────────────────────


def test_cooldown_blocks_subsequent_calls():
    """After a 429, the next call blocks until cooldown expires."""
    cfg = QueueConfig(
        max_concurrency=1,
        max_retries=0,
        cooldown_secs=0.2,
    )
    q = RequestQueue(cfg, name="t")

    def always_429():
        raise RuntimeError("HTTP 429")

    def quick():
        return "ok"

    # First call: 429, no retries, enters cooldown.
    with pytest.raises(RuntimeError):
        q.submit_sync(always_429)

    start = time.time()
    # Second call should block for ~0.2s.
    result = q.submit_sync(quick)
    elapsed = time.time() - start
    assert result == "ok"
    assert elapsed >= 0.15  # allow some tolerance


# ── Provider wrapper ──────────────────────────────────────────────────────


class FakeProvider:
    """Minimal provider stub for wrap_provider tests."""

    provider_id = "test-provider"

    def __init__(self, generate_return="response", stream_chunks=("chunk",)):
        self._generate_return = generate_return
        self._stream_chunks = stream_chunks
        self.generate_calls = 0
        self.stream_calls = 0

    async def generate(self, messages, model=None):
        self.generate_calls += 1
        return self._generate_return

    def stream(self, messages, model=None):
        self.stream_calls += 1
        return iter(self._stream_chunks)


@pytest.mark.asyncio
async def test_wrap_provider_routes_through_queue():
    """Wrapped provider.generate goes through the queue."""
    provider = FakeProvider()

    q = wrap_provider(provider, QueueConfig(max_concurrency=1))
    assert hasattr(provider, "_unwrapped_generate")
    assert hasattr(provider, "_request_queue")

    result = await provider.generate([{"role": "user", "content": "hi"}])
    assert result == "response"
    assert q.stats()["completed"] == 1

    # Stream also goes through the queue.
    chunks = list(provider.stream([{"role": "user", "content": "hi"}]))
    assert chunks == ["chunk"]
    assert q.stats()["completed"] == 2


@pytest.mark.asyncio
async def test_unwrap_provider_restores_original_methods():
    provider = FakeProvider()
    original_generate = provider.generate
    original_stream = provider.stream

    wrap_provider(provider, QueueConfig(max_concurrency=1))
    # After wrapping, the methods are replaced with wrappers.
    assert provider.generate != original_generate
    assert provider.stream != original_stream

    unwrap_provider(provider)
    # Bound methods don't compare with `is` (each access creates a new
    # bound-method object), but `==` works because Python's bound-method
    # __eq__ compares the underlying function and the instance.
    assert provider.generate == original_generate
    assert provider.stream == original_stream
    assert not hasattr(provider, "_unwrapped_generate")
    assert not hasattr(provider, "_request_queue")


@pytest.mark.asyncio
async def test_wrap_provider_idempotent():
    """Wrapping an already-wrapped provider is a no-op."""
    provider = FakeProvider()

    q1 = wrap_provider(provider)
    q2 = wrap_provider(provider)
    assert q1 is q2  # same queue returned


@pytest.mark.asyncio
async def test_wrap_provider_retries_on_rate_limit():
    """A wrapped provider retries rate-limited calls."""
    attempts = {"n": 0}

    class FlakyProvider:
        provider_id = "flaky"

        async def generate(self, messages, model=None):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("HTTP 429 too many requests")
            return "ok"

    provider = FlakyProvider()
    cfg = QueueConfig(
        max_concurrency=1,
        max_retries=2,
        retry_backoff_secs=0.01,
        cooldown_secs=0.01,
    )
    wrap_provider(provider, cfg)

    result = await provider.generate([{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert attempts["n"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
