"""A TokenBucket wrapper that also rotates proxies and API keys.

The dependency points from contrib to core and never back — the invariant
``tests/conformance/test_evasion_quarantine.py`` protects. Task 10 found what
happens when it does not: a limiter that could see the key pool used it to
suppress its own backoff, declining to slow down because another key was free.
"""

from __future__ import annotations

import logging

from crocodile.contrib.evasion.api_key import ApiKeyPool
from crocodile.contrib.evasion.proxy import ProxyRotator
from crocodile.core.ratelimit import TokenBucket

logger = logging.getLogger(__name__)

__all__ = ["EvasiveLimiter"]


class EvasiveLimiter:
    """Pairs a core :class:`TokenBucket` with proxy rotation and API-key pooling.

    The bucket paces outbound requests; the rotator and the pool decide which
    identity carries them. The two concerns are composed here rather than in the
    bucket, so that ``crocodile.core`` never learns that spare identities exist.

    Rotating an identity does not excuse the bucket from backing off. A 429 is
    the source saying *slow down*, and :meth:`update_backoff` applies it to the
    bucket whether or not another key or proxy is available.
    """

    def __init__(
        self,
        bucket: TokenBucket,
        proxy_rotator: ProxyRotator | None = None,
        api_key_pool: ApiKeyPool | None = None,
        provider: str | None = None,
    ) -> None:
        """Compose a bucket with the optional identity pools.

        Args:
            bucket: The core rate limiter doing the pacing.
            proxy_rotator: Optional ProxyRotator managing proxy URLs.
            api_key_pool: Optional ApiKeyPool managing multiple API keys.
            provider: Default provider name for key lookups. The core bucket no
                longer carries this — it existed only to feed key-pool lookups —
                so the contrib side supplies it.
        """
        self._bucket = bucket
        self._proxy_rotator = proxy_rotator
        self._api_key_pool = api_key_pool
        self._provider = provider

    @property
    def bucket(self) -> TokenBucket:
        """The core TokenBucket this limiter paces with."""
        return self._bucket

    @property
    def proxy_rotator(self) -> ProxyRotator | None:
        """The ProxyRotator associated with this limiter."""
        return self._proxy_rotator

    @property
    def api_key_pool(self) -> ApiKeyPool | None:
        """The ApiKeyPool associated with this limiter."""
        return self._api_key_pool

    @property
    def provider(self) -> str | None:
        """The default API provider name associated with this limiter."""
        return self._provider

    @property
    def tokens(self) -> float:
        """The bucket's current number of available tokens."""
        return self._bucket.tokens

    @property
    def backoff_remaining(self) -> float:
        """The bucket's remaining backoff duration in seconds."""
        return self._bucket.backoff_remaining

    @property
    def is_backed_off(self) -> bool:
        """Whether the bucket is currently under a backoff delay."""
        return self._bucket.is_backed_off

    async def acquire(self, tokens: float = 1.0) -> None:
        """Acquire tokens from the bucket, blocking asynchronously if necessary.

        Args:
            tokens: Number of tokens to acquire.
        """
        await self._bucket.acquire(tokens)

    def get_proxy(self) -> str | None:
        """Get the current active proxy from the rotator."""
        if self._proxy_rotator:
            return self._proxy_rotator.get_proxy()
        return None

    def rotate_proxy(self) -> str | None:
        """Force rotate to the next proxy."""
        if self._proxy_rotator:
            return self._proxy_rotator.rotate()
        return None

    def report_proxy_failure(self, proxy: str) -> None:
        """Report a failure or timeout for a specific proxy."""
        if self._proxy_rotator:
            self._proxy_rotator.report_failure(proxy)

    def get_api_key(self, provider: str | None = None) -> str | None:
        """Get an active API key from the pool.

        Args:
            provider: The API provider name. Defaults to the limiter's default provider.
        """
        p = provider or self._provider
        if not p:
            logger.warning("No provider specified or set on the limiter.")
            return None
        if self._api_key_pool:
            return self._api_key_pool.get_key(p)
        return None

    def report_key_success(self, key: str, provider: str | None = None) -> None:
        """Report a successful request with a key."""
        p = provider or self._provider
        if p and self._api_key_pool:
            self._api_key_pool.report_success(p, key)

    def report_key_failure(self, key: str, provider: str | None = None) -> None:
        """Report a general failure with a key."""
        p = provider or self._provider
        if p and self._api_key_pool:
            self._api_key_pool.report_failure(p, key)

    def report_key_throttled(
        self,
        key: str,
        backoff_duration: float,
        provider: str | None = None,
    ) -> None:
        """Report that a key was throttled."""
        p = provider or self._provider
        if p and self._api_key_pool:
            self._api_key_pool.report_throttled(p, key, backoff_duration)

    def report_key_exhausted(
        self,
        key: str,
        reset_in: float = 86400.0,
        provider: str | None = None,
    ) -> None:
        """Report that a key has hit its daily/monthly cap."""
        p = provider or self._provider
        if p and self._api_key_pool:
            self._api_key_pool.report_exhausted(p, key, reset_in)

    def update_key_quota(
        self,
        key: str,
        remaining: int,
        limit: int | None = None,
        reset_at_epoch: float | None = None,
        provider: str | None = None,
    ) -> None:
        """Update quota information for a key."""
        p = provider or self._provider
        if p and self._api_key_pool:
            self._api_key_pool.update_quota(p, key, remaining, limit, reset_at_epoch)

    def update_backoff(
        self,
        delay: float,
        key: str | None = None,
        proxy: str | None = None,
        provider: str | None = None,
    ) -> None:
        """Back the bucket off, and mark the key and proxy that earned the 429.

        The backoff reaches the bucket unconditionally. The version of this that
        lived on the bucket counted spare keys and idle proxies first and skipped
        its own backoff when it found one, which is quota evasion wearing a rate
        limiter's name. Identities rotate; the pacing still applies.

        Args:
            delay: The backoff delay in seconds, applied to the bucket.
            key: If provided, reports the API key as throttled in the API key pool.
            proxy: If provided, reports the proxy as failed in the proxy rotator.
            provider: Override or specify the provider for the API key pool.
        """
        if key:
            self.report_key_throttled(key, delay, provider)
        if proxy:
            self.report_proxy_failure(proxy)
        self._bucket.update_backoff(delay)
