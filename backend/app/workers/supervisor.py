"""RH-3 — supervised worker runtime."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence

logger = logging.getLogger("app.workers.supervisor")

INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
BACKOFF_MULTIPLIER = 2.0
HEALTHY_UPTIME_SECONDS = 120.0
JOIN_POLL_SECONDS = 0.5


class ShutdownFlag(Protocol):
    requested: bool


@dataclass
class LoopSpec:
    name: str
    runner: Callable[..., Any]
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class _LoopState:
    spec: LoopSpec
    thread: Optional[threading.Thread] = None
    restarts: int = 0
    backoff: float = INITIAL_BACKOFF_SECONDS
    started_at: float = 0.0
    retired: bool = False
    last_error: Optional[str] = None
    fatal: bool = False


def _run_once(state: _LoopState, shutdown: ShutdownFlag) -> None:
    name = state.spec.name
    state.started_at = time.monotonic()
    try:
        state.spec.runner(**state.spec.kwargs)
    except SystemExit as exc:
        code = int(getattr(exc, "code", 0) or 0)
        if code == 0:
            state.retired = True
            logger.info("supervisor.loop_exited", extra={"loop": name})
        else:
            state.last_error = f"SystemExit({code})"
            logger.error("supervisor.loop_exited_nonzero", extra={"loop": name, "code": code})
        return
    except BaseException as exc:  # noqa: BLE001
        state.last_error = f"{type(exc).__name__}: {exc}"
        logger.exception("supervisor.loop_crashed", extra={"loop": name, "restarts": state.restarts})
        return

    state.retired = True
    logger.info("supervisor.loop_drained", extra={"loop": name, "uptime_s": round(time.monotonic() - state.started_at, 1)})


def _spawn(state: _LoopState, shutdown: ShutdownFlag) -> None:
    thread = threading.Thread(
        target=_run_once,
        args=(state, shutdown),
        name=f"flowpilot-{state.spec.name}",
        daemon=True,
    )
    state.thread = thread
    thread.start()
    logger.info("supervisor.loop_started", extra={"loop": state.spec.name, "restarts": state.restarts})


def run_supervised(
    specs: Sequence[LoopSpec],
    *,
    shutdown: ShutdownFlag,
    max_restarts: int = 0,
    drain_timeout_seconds: float = 45.0,
) -> int:
    if not specs:
        raise ValueError("run_supervised() requires at least one LoopSpec")

    names = [spec.name for spec in specs]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate loop names: {names}")

    states = [_LoopState(spec=spec) for spec in specs]
    logger.info("supervisor.start", extra={"loops": names, "max_restarts": max_restarts})

    for state in states:
        _spawn(state, shutdown)

    pending_respawn: dict[str, float] = {}

    try:
        while not shutdown.requested:
            time.sleep(JOIN_POLL_SECONDS)
            now = time.monotonic()

            for state in states:
                name = state.spec.name

                if state.fatal or state.retired:
                    continue

                due = pending_respawn.get(name)
                if due is not None:
                    if now >= due:
                        del pending_respawn[name]
                        _spawn(state, shutdown)
                    continue

                thread = state.thread
                if thread is not None and thread.is_alive():
                    if (
                        state.backoff > INITIAL_BACKOFF_SECONDS
                        and now - state.started_at >= HEALTHY_UPTIME_SECONDS
                    ):
                        logger.info("supervisor.backoff_reset", extra={"loop": name, "uptime_s": round(now - state.started_at, 1)})
                        state.backoff = INITIAL_BACKOFF_SECONDS
                    continue

                if shutdown.requested:
                    break

                state.restarts += 1
                if max_restarts and state.restarts > max_restarts:
                    state.fatal = True
                    logger.critical("supervisor.loop_abandoned", extra={"loop": name, "restarts": state.restarts, "last_error": state.last_error})
                    continue

                delay = state.backoff
                state.backoff = min(state.backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF_SECONDS)
                pending_respawn[name] = now + delay
                logger.warning("supervisor.loop_restarting", extra={"loop": name, "restarts": state.restarts, "delay_s": round(delay, 1), "last_error": state.last_error})

            if all(state.retired or state.fatal for state in states):
                logger.info("supervisor.all_loops_settled")
                break

    except KeyboardInterrupt:
        logger.info("supervisor.keyboard_interrupt")
        try:
            shutdown.requested = True
        except Exception:  # noqa: BLE001
            pass

    logger.info("supervisor.draining", extra={"timeout_s": drain_timeout_seconds})

    deadline = time.monotonic() + drain_timeout_seconds
    for state in states:
        thread = state.thread
        if thread is None:
            continue
        while thread.is_alive() and time.monotonic() < deadline:
            thread.join(timeout=JOIN_POLL_SECONDS)
        if thread.is_alive():
            logger.warning("supervisor.drain_timeout", extra={"loop": state.spec.name})

    abandoned = [state.spec.name for state in states if state.fatal]
    if abandoned:
        logger.critical("supervisor.stopped_degraded", extra={"abandoned": abandoned})
        return 1

    logger.info("supervisor.stopped")
    return 0


__all__ = [
    "LoopSpec",
    "ShutdownFlag",
    "run_supervised",
    "INITIAL_BACKOFF_SECONDS",
    "MAX_BACKOFF_SECONDS",
    "HEALTHY_UPTIME_SECONDS",
]