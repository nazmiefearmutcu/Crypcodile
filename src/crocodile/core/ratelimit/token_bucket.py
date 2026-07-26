"""A proactive outbound rate limiter, shared by every asset class.

The bucket paces requests before they leave, rather than reacting to the 429 a
venue sends back. It knows about tokens, waiters, and backoff — and deliberately
nothing about proxies or API-key pools, which live in ``crocodile.contrib.evasion``
and compose with this from that side, never the reverse.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from collections.abc import Callable

__all__ = ["TokenBucket", "TokenBucketLimiter"]


class TokenBucket:
    """A thread-safe-ish (asyncio event loop bound) Token Bucket rate limiter.

    Supports capacity, refill rate, async acquire, and handling of HTTP 429
    backoff delays.
    """

    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        initial_tokens: float | None = None,
        time_func: Callable[[], float] = time.monotonic,
        greedy: bool = False,
    ) -> None:
        """Initialize the Token Bucket.

        Args:
            capacity: Maximum number of tokens the bucket can hold.
            refill_rate: Number of tokens added to the bucket per second.
            initial_tokens: Initial number of tokens in the bucket. Defaults to capacity.
            time_func: Function providing current time in seconds (defaults to time.monotonic).
            greedy: If True, uses greedy allocation bypassing head-of-line blocking.
        """
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        if refill_rate <= 0:
            raise ValueError("Refill rate must be positive")

        self._capacity = float(capacity)
        self._refill_rate = float(refill_rate)

        if initial_tokens is None:
            self._tokens = self._capacity
        else:
            self._tokens = float(initial_tokens)
            if self._tokens < 0.0 or self._tokens > self._capacity:
                raise ValueError("Initial tokens must be between 0 and capacity")

        self._time_func = time_func
        self._last_refill = time_func()
        self._backoff_until = 0.0
        self._waiters: deque[tuple[float, asyncio.Future[None]]] = deque()

        self._lock = threading.Lock()
        self._timers: dict[asyncio.AbstractEventLoop, asyncio.TimerHandle] = {}
        self._greedy = greedy

    @property
    def capacity(self) -> float:
        """The maximum token capacity of the bucket."""
        return self._capacity

    @property
    def refill_rate(self) -> float:
        """The rate at which tokens are refilled per second."""
        return self._refill_rate

    @property
    def tokens(self) -> float:
        """The current number of available tokens, accounting for refill and backoff."""
        with self._lock:
            now = self._time_func()
            if now < self._backoff_until:
                return 0.0
            if now <= self._last_refill:
                return self._tokens
            elapsed = now - self._last_refill
            return min(self._capacity, self._tokens + elapsed * self._refill_rate)

    @property
    def backoff_remaining(self) -> float:
        """The remaining backoff duration in seconds."""
        with self._lock:
            now = self._time_func()
            if now >= self._backoff_until:
                return 0.0
            return self._backoff_until - now

    @property
    def is_backed_off(self) -> bool:
        """Whether the bucket is currently under a backoff delay."""
        with self._lock:
            return self._time_func() < self._backoff_until

    def update_backoff(self, delay: float) -> None:
        """Temporarily pause request acquisition by setting an HTTP 429 backoff delay.

        During the backoff period, the bucket behaves as if it has 0 tokens, and any pending
        or new acquisitions will be delayed until the backoff expires.

        Args:
            delay: The backoff delay in seconds.
        """
        if delay < 0:
            raise ValueError("Backoff delay must be non-negative")

        with self._lock:
            now = self._time_func()
            self._backoff_until = max(self._backoff_until, now + delay)
            self._tokens = 0.0
            self._last_refill = max(self._last_refill, self._backoff_until)

            loops = {w[1].get_loop() for w in self._waiters if not w[1].done()}

        # Trigger process queue in all event loops with active waiters
        for loop in loops:
            try:
                loop.call_soon_threadsafe(self._process_queue)
            except RuntimeError:
                pass

        try:
            current_loop = asyncio.get_running_loop()
            if current_loop not in loops and current_loop.is_running():
                current_loop.call_soon(self._process_queue)
        except RuntimeError:
            pass

    async def acquire(self, tokens: float = 1.0) -> None:
        """Acquire the specified number of tokens, blocking asynchronously if necessary.

        Args:
            tokens: Number of tokens to acquire. Must be non-negative and <= capacity.
        """
        if tokens < 0:
            raise ValueError("Requested tokens must be non-negative")
        if tokens > self._capacity:
            raise ValueError(f"Requested tokens {tokens} exceeds bucket capacity {self._capacity}")

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        with self._lock:
            self._waiters.append((tokens, future))
            future.add_done_callback(self._waiter_done)

        self._process_queue()
        await future

    def _waiter_done(self, future: asyncio.Future[None]) -> None:
        """Callback triggered when a waiter future is resolved or cancelled."""
        with self._lock:
            for item in list(self._waiters):
                if item[1] is future:
                    self._waiters.remove(item)
                    break
        self._process_queue()

    def _on_timer(self, loop: asyncio.AbstractEventLoop) -> None:
        """Callback triggered when a scheduled refill or backoff timer fires."""
        with self._lock:
            if loop in self._timers:
                self._timers.pop(loop)
        self._process_queue()

    def _process_queue(self) -> None:
        """Process the waiter queue and satisfy or schedule waiters as appropriate."""
        with self._lock:
            for _loop, timer in list(self._timers.items()):
                timer.cancel()
            self._timers.clear()

            now = self._time_func()

            # Refill tokens up to now, if we are not in a backoff period
            if now > self._last_refill:
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._last_refill) * self._refill_rate
                )
                self._last_refill = now

            while self._waiters and self._waiters[0][1].done():
                self._waiters.popleft()

            if not self._waiters:
                return

            if now < self._backoff_until:
                wait_time = self._backoff_until - now + 1e-3
                loops = {w[1].get_loop() for w in self._waiters if not w[1].done()}
                for loop in loops:
                    try:
                        self._timers[loop] = loop.call_later(wait_time, self._on_timer, loop)
                    except RuntimeError:
                        pass
                return

            if self._greedy:
                new_waiters: deque[tuple[float, asyncio.Future[None]]] = deque()
                for tokens, future in self._waiters:
                    if future.done():
                        continue
                    if self._tokens >= tokens:
                        self._tokens -= tokens
                        if not future.done():
                            future.get_loop().call_soon_threadsafe(future.set_result, None)
                    else:
                        new_waiters.append((tokens, future))
                self._waiters = new_waiters

                # Schedule timers for the remaining waiters
                loop_to_waiter: dict[asyncio.AbstractEventLoop, float] = {}
                for tokens, future in self._waiters:
                    loop = future.get_loop()
                    if loop not in loop_to_waiter:
                        loop_to_waiter[loop] = tokens

                for loop, tokens in loop_to_waiter.items():
                    needed = tokens - self._tokens
                    wait_time = (needed / self._refill_rate) + 1e-3
                    try:
                        self._timers[loop] = loop.call_later(wait_time, self._on_timer, loop)
                    except RuntimeError:
                        pass
            else:
                while self._waiters:
                    tokens, future = self._waiters[0]
                    if future.done():
                        self._waiters.popleft()
                        continue

                    if self._tokens >= tokens:
                        self._tokens -= tokens
                        self._waiters.popleft()
                        if not future.done():
                            future.get_loop().call_soon_threadsafe(future.set_result, None)
                    else:
                        needed = tokens - self._tokens
                        wait_time = (needed / self._refill_rate) + 1e-3
                        loop = future.get_loop()
                        try:
                            self._timers[loop] = loop.call_later(wait_time, self._on_timer, loop)
                        except RuntimeError:
                            pass
                        return


class TokenBucketLimiter(TokenBucket):
    """An alias of TokenBucket matching the TokenBucketLimiter name, for compatibility.

    Accepts `rate` instead of `refill_rate`.
    """

    def __init__(
        self,
        rate: float,
        capacity: float,
        initial_tokens: float | None = None,
        time_func: Callable[[], float] = time.monotonic,
        greedy: bool = False,
    ) -> None:
        """Initialize TokenBucketLimiter."""
        super().__init__(
            capacity=capacity,
            refill_rate=rate,
            initial_tokens=initial_tokens,
            time_func=time_func,
            greedy=greedy,
        )
