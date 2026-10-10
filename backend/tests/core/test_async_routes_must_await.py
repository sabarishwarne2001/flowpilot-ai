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

F-218 closed three ways this rule was satisfied on paper only:

* an await inside a nested function (a stream's generator) counted for the
  route around it, which then did all its database work on the loop;
* a route awaited one of our own coroutines that never awaits anything (the
  assistant's `send_chat_message`), so retrieval and the model call ran on the
  loop all the same;
* dependencies were never checked: every authenticated request ran the user,
  session and API key lookups in an `async def` dependency.

So the route's OWN body must await, the coroutines it awaits from `app` must
themselves await, and async dependencies obey the same rule.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

from fastapi.routing import APIRoute

from app.main import app

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: async routes that do no blocking work, so running them on the loop is right.
NO_IO_ASYNC_ROUTES = {
    # Liveness: answers from settings only. Kept on the loop so it still
    # answers when the thread pool is saturated.
    "app.api.v1.health.get_health",
}


#: async dependencies that do no blocking work: they read attributes the user
#: lookup already loaded.
NO_IO_ASYNC_DEPENDENCIES = {
    "app.api.deps.get_current_active_user",
    "app.api.deps.get_verified_user",
    "app.api.deps.require_superadmin",
}

_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_AWAITING = (ast.Await, ast.AsyncFor, ast.AsyncWith)


def _own_nodes(function: ast.AST):
    """Every node in the function's own body, not in a function nested inside it."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _NESTED):
            stack.extend(ast.iter_child_nodes(node))


def _function_node(func) -> ast.AST:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))


def _awaits(func) -> bool:
    return any(isinstance(node, _AWAITING) for node in _own_nodes(_function_node(func)))


def _hollow_coroutines() -> set[str]:
    """Names of the `async def`s in app/ whose own body never awaits."""
    hollow: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.AsyncFunctionDef) and not any(isinstance(n, _AWAITING) for n in _own_nodes(node)):
                if not any(isinstance(n, ast.Yield) for n in _own_nodes(node)):
                    hollow.add(node.name)
    return hollow


def _awaited_names(func) -> set[str]:
    names = set()
    for node in _own_nodes(_function_node(func)):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            target = node.value.func
            if isinstance(target, ast.Attribute):
                names.add(target.attr)
            elif isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _dependencies(dependant, seen: set) -> list:
    found = []
    for sub in dependant.dependencies:
        call = sub.call
        if call is not None and id(call) not in seen:
            seen.add(id(call))
            found.append(call)
        found.extend(_dependencies(sub, seen))
    return found


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


def test_no_async_route_awaits_a_coroutine_that_never_awaits() -> None:
    hollow = _hollow_coroutines()
    offenders = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not inspect.iscoroutinefunction(route.endpoint):
            continue
        for name in sorted(_awaited_names(route.endpoint) & hollow):
            offenders.append(f"{sorted(route.methods)} {route.path} awaits {name}()")
    assert not offenders, (
        "These routes await one of our coroutines that never awaits anything itself, so the "
        "work inside it runs on the event loop (F-218). Make it a plain function and call it "
        "from a `def` route, or from the threadpool:\n  " + "\n  ".join(offenders)
    )


def test_every_async_dependency_awaits_something() -> None:
    seen: set = set()
    offenders = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for call in _dependencies(route.dependant, seen):
            function = call if inspect.isfunction(call) else getattr(call, "__call__", None)
            if function is None or not inspect.iscoroutinefunction(function):
                continue
            name = f"{function.__module__}.{function.__qualname__}"
            if not function.__module__.startswith("app.") or name in NO_IO_ASYNC_DEPENDENCIES:
                continue
            if not _awaits(function):
                offenders.add(name)
    assert not offenders, (
        "These dependencies are `async def` but never await, so their database work blocks "
        "the event loop for every request that uses them (F-218). Make them plain `def`, or "
        "run their blocking work in the threadpool:\n  " + "\n  ".join(sorted(offenders))
    )
