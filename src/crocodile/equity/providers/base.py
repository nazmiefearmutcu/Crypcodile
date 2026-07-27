from __future__ import annotations

import asyncio
import json
import logging
import random
import traceback
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar

from crocodile.core.ingest.deadletter import DeadLetterQueue
from crocodile.core.ingest.transport import Transport
from crocodile.core.schema.records import Record

if TYPE_CHECKING:
    from crocodile.core.sink.base import Sink
    from crocodile.equity.reference.identity import InstrumentIdentity
    from crocodile.equity.reference.registry import InstrumentRegistry

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Base class for provider-level errors."""


class FatalProviderError(ProviderError):
    """Non-recoverable provider error — do not reconnect (auth, config, etc.)."""


class TransientProviderError(ProviderError):
    """Recoverable provider error — reconnect with backoff is appropriate."""


class SinkError(ProviderError):
    """Failure writing to the output sink (not a bad market-data frame)."""


def backoff_delays(
    attempt: int,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.25,
    rand: float = 0.0,
) -> float:
    raw = min(cap, base * float(2**attempt))
    # Apply the cap AFTER jitter too: jitter can otherwise push a capped delay
    # above `cap` (e.g. raw=30, jitter=0.25, rand→1.0 → 37.5s), defeating the
    # documented ceiling during sustained reconnect failures.
    return min(cap, raw * (1.0 + jitter * rand))


class Provider(ABC):
    name: str
    ws_url: str
    rest_url: str

    supported_channels: ClassVar[frozenset[str] | None] = None
    """Every channel this provider can actually serve, or ``None`` for "not declared".

    ``None`` is not "all": it means this connector has not been through the exercise
    and nothing is enforced for it, which is where every connector starts. Declaring
    the set turns it into a contract — :meth:`_reject_unservable_channels` warns for
    each requested channel outside it and refuses the run when none survives, which is
    what :class:`~crocodile.equity.providers.finnhub.connector.FinnhubProvider` already
    does by hand for its tier-dependent set.

    The alternative is what ``google_finance --channels quote`` did after its ``Quote``
    was removed: poll forever, four fetches per symbol per cycle, and return nothing.
    A channel that is configured, never errors and never produces a row is
    indistinguishable from a market with nothing to report.
    """

    unservable_channels: ClassVar[Mapping[str, str]] = MappingProxyType({})
    """Channels this provider is *asked* for and deliberately does not serve, and why.

    A decision, recorded where the decision lives. An entry here is a capability the
    connector could plausibly be expected to have and does not, with the argument —
    the same discipline :data:`crocodile.core.capability.IRREDUCIBLE` carries. The
    reason is emitted in the warning and in the refusal, so a user who asks for the
    channel is told why rather than left with an empty lake.
    """

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
    ) -> None:
        self.symbols = symbols
        self.channels = channels
        self.out = out
        self.registry = registry
        self.transport: Transport | None = None
        self._dlq: DeadLetterQueue = DeadLetterQueue()
        self._reject_unservable_channels()

    def _reject_unservable_channels(self) -> None:
        """Warn for each requested channel this provider cannot serve; refuse if none can.

        A no-op for a connector that has not declared :attr:`supported_channels`.

        Raises:
            ValueError: if every requested channel is unservable. Constructed here
                rather than at the first poll so the CLI reports it before opening a
                session — ``collect`` already turns a ``ValueError`` from the factory
                into a message and a non-zero exit.
        """
        if self.supported_channels is None:
            return
        unservable = [ch for ch in self.channels if ch not in self.supported_channels]
        for channel in unservable:
            log.warning(
                "%s cannot serve the %r channel and will emit nothing for it%s",
                self.name,
                channel,
                f": {self.unservable_channels[channel]}"
                if channel in self.unservable_channels
                else "",
            )
        if unservable and len(unservable) == len(self.channels):
            raise ValueError(
                f"{self.name} has no supported channels among {self.channels!r} "
                f"(supported: {sorted(self.supported_channels)})"
            )

    @abstractmethod
    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]: ...

    @abstractmethod
    async def list_instruments(self) -> list[InstrumentIdentity]: ...

    def subscribe_channels(self) -> list[str] | list[dict[str, str]]:
        """Return the WS channel descriptors this connector will subscribe to.

        Override in concrete connectors.  Not abstract so that future connectors
        are not forced to implement it before they are ready.
        """
        raise NotImplementedError

    @abstractmethod
    async def _subscribe(self, transport: Transport) -> None:
        """Send exchange-specific subscribe frames over *transport*.

        Each exchange uses a completely different wire format for subscription
        (Deribit: JSON-RPC 2.0 ``public/subscribe``; Binance: ``{"method":
        "SUBSCRIBE", "params": [...]}``; Bybit/OKX/Coinbase differ again —
        appendix §4 table, §3.2).  This method is therefore abstract: every
        concrete connector is responsible for composing and sending its own
        subscribe frame(s).  A connector that needs no subscription (e.g. a
        pure pull source) should implement an explicit no-op.
        """

    async def backfill(
        self,
        channel: str,
        symbol: str,
        start_ns: int,
        end_ns: int,
    ) -> AsyncIterator[Record]:
        raise NotImplementedError
        yield  # pragma: no cover  (makes this an async generator)

    async def run(self, max_reconnects: int = -1) -> None:
        """Supervised run loop.

        Connects the transport, subscribes, then drains frames into the sink.
        On exception: exponential backoff then reconnect (up to max_reconnects).
        max_reconnects=-1 means unlimited; max_reconnects=0 means no reconnect.
        Unparseable frames go to the DLQ; the loop continues.
        ``transport.close()`` is always called — on clean exit and on exception
        — via a ``try/finally`` block so that socket handles are never leaked.
        """
        attempt = 0
        while True:
            transport = self.transport
            if transport is None:
                raise RuntimeError("No transport configured; set provider.transport before run()")
            try:
                await transport.connect()
                await self._subscribe(transport)
                # Successful connect/subscribe resets consecutive-failure budget
                attempt = 0

                async for raw in transport:
                    # Use standard time_ns for local timestamp
                    import time

                    local_ts = time.time_ns()
                    try:
                        msg = json.loads(raw)
                    except Exception as exc:
                        tb = traceback.format_exc()
                        self._dlq.put(local_ts, raw, type(exc).__name__, tb)
                        log.debug("DLQ: unparseable frame: %s", exc)
                        continue

                    if isinstance(msg, dict) and msg.get("error") is not None:
                        log.warning("%s: provider rejected request: %s", self.name, msg["error"])
                        continue

                    try:
                        records = list(self.normalize(msg, local_ts))
                    except FatalProviderError:
                        raise
                    except Exception as exc:
                        tb = traceback.format_exc()
                        self._dlq.put(local_ts, raw, type(exc).__name__, tb)
                        log.debug("DLQ: normalize error: %s", exc)
                        continue

                    for rec in records:
                        try:
                            await self.out.put(rec)
                        except Exception as exc:
                            # Sink failures are not bad frames — do not DLQ
                            raise SinkError(
                                f"Sink put failed for {self.name}: {exc}"
                            ) from exc

                # Transport exhausted normally (StopAsyncIteration) -> done
                break

            except FatalProviderError:
                log.error("Provider %s fatal error — not reconnecting", self.name)
                raise
            except Exception as exc:
                log.warning("Provider %s error (attempt %d): %s", self.name, attempt, exc)
                if max_reconnects == 0 or (max_reconnects > 0 and attempt >= max_reconnects):
                    raise
                delay = backoff_delays(attempt, jitter=0.25, rand=random.random())
                log.info("Reconnecting in %.2fs...", delay)
                await asyncio.sleep(delay)
                attempt += 1
            finally:
                await transport.close()
