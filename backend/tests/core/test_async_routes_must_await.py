"""F-212 guard — an `async def` route that never awaits runs its blocking work on
the event loop.

    pytest tests/core/test_async_routes_must_await.py -q

The database session is synchronous. In a plain `def` route FastAPI runs the
handler in its thread pool, so a query that waits (for a row lock, a slow
statement) holds one worker thread. In an `async def` route the same query runs
ON the event-loop thread and every other request waits with it; when the query
waits for a lock held by a request that needs the loop to finish, the process
never recovers (F-212: a double click on "Change role" froze the whole API).

So a route is `async def` only when it awaits something (an upload body, a
stream, a websocket). Routes that do no I/O at all may stay async; they are
listed here by name with the reason.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from fastapi.routing import APIRoute

from app.main import app

#: async routes that do no blocking work, so running them on the loop is right.
NO_IO_ASYNC_ROUTES = {
    # Liveness: answers from settings only. Kept on the loop so it still
    # answers when the thread pool is saturated.
    "app.api.v1.health.get_health",
}


def _awaits(func) -> bool:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return any(isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for node in ast.walk(tree))


def test_every_async_route_awaits_something() -> None:
    offenders = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        endpoint = route.endpoint
        if not inspect.iscoroutinefunction(endpoint):
            continue
        name = f"{endpoint.__module__}.{endpoint.__qualname__}"
        if name in NO_IO_ASYNC_ROUTES:
            continue
        if not _awaits(endpoint):
            offenders.append(f"{sorted(route.methods)} {route.path} -> {name}")
    assert not offenders, (
        "These routes are `async def` but never await, so their database work blocks "
        "the event loop (F-212). Make them plain `def`:\n  " + "\n  ".join(offenders)
    )
