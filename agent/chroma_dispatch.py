"""Off-event-loop dispatch for search/fetch calls that reach the vector
store.

Every REST route and every MCP tool dispatch is served by the SAME request
event loop. A synchronous call into the vector store (query/get) can block
for as long as the store's own internal work takes — including while it is
compacting a large collection — and while it does, that one blocked call
holds the entire loop, so no other request, not even an unrelated one, can
be served until it returns.

``dispatch_chroma_sync``/``dispatch_chroma_async`` route such a search/fetch
call through ONE dedicated single-worker executor owned by the service,
instead of running it in place on the caller's thread. A single worker
preserves the request-at-a-time ordering these calls already had while they
all ran directly on the one event-loop thread; dispatching to a bare/
unbounded thread pool would let two vector-store-touching requests run
concurrently for the first time — a new race this design avoids. Bulk
operations such as a full-workspace index are deliberately NOT routed
through this module — they already run on their own separate thread pool
and sharing this single-worker executor with them would serialize search/
fetch behind however long the bulk operation takes.

Callers pass the owning service object itself (not the executor), so a test
double built without a real executor attached — most unit tests construct a
plain mock or a partially-initialised service instance — is handled without
any special test wiring: `dispatch_chroma_async` (see below) falls back to a
generic off-loop dispatch that still never runs `fn` on the calling
coroutine's event-loop thread; only a REAL service, with its own dedicated
single-worker executor, gets the stronger same-request-order serialization
guarantee on top of that.
"""
from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def _executor_of(service: Any) -> ThreadPoolExecutor | None:
    executor = getattr(service, "_chroma_executor", None)
    return executor if isinstance(executor, ThreadPoolExecutor) else None


def dispatch_chroma_sync(service: Any, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run ``fn(*args, **kwargs)`` on ``service``'s dedicated vector-store
    executor and block until it completes.

    For callers that are already running off the event loop themselves (the
    MCP transport's synchronous tool dispatcher is itself reached via its
    own thread-pool hop) but are plain functions, not coroutines, and so
    cannot ``await`` the async variant below. Because such a caller is
    already off-loop, calling ``fn`` in place when no dedicated executor is
    attached (test doubles) is safe — it never touches an event-loop thread
    either way.
    """
    executor = _executor_of(service)
    if executor is None:
        return fn(*args, **kwargs)
    return executor.submit(fn, *args, **kwargs).result()


async def dispatch_chroma_async(service: Any, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Await ``fn(*args, **kwargs)`` off the calling coroutine's event-loop
    thread, without ever running it in place.

    Dispatched to ``service``'s dedicated single-worker executor when one is
    attached (every real `VectrService`), for the same-request-order
    serialization guarantee described in this module's docstring. Falls back
    to the running loop's own default executor for a service with none
    attached (a test double) — still off-loop, just without that
    serialization guarantee, which only matters for real, shared indexer/
    searcher state that a test double doesn't have.

    For async REST route handlers.
    """
    loop = asyncio.get_running_loop()
    executor = _executor_of(service)
    return await loop.run_in_executor(executor, functools.partial(fn, *args, **kwargs))
