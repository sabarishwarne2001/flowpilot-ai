#!/usr/bin/env python3
"""ARCH-39 — Conversational AI Suite & Production RAG Hardening: the gates.

    python verify_arch39.py                   offline gates
    python verify_arch39.py --build           + tsc, eslint on touched files, vite build
    python verify_arch39.py --db              + live Postgres gates (run after
                                                `alembic upgrade head`)
    python verify_arch39.py --mutate          + mutation kills
    python verify_arch39.py --skip-regressions

Regression: verify_arch36.py (it chains 35 → 31), with --db when --db is
set. apply_arch39.py widens the head those scripts pin, as ARCH-35 did.

EXIT 0 pass | 1 a gate failed | 2 harness could not run

WHAT IS EXERCISED, NOT JUST READ
================================

  * rag_guard is loaded from its file and driven through every branch,
    including a Groq-shaped 413 wrapped the way both chat paths wrap it.
  * LLMService.synthesize_response runs with a fake provider that refuses the
    first request as too large: the gate asserts one smaller retry, a
    learned limit, and `context_trimmed`, and that a 200-turn history no
    longer produces an unbounded prompt.
  * AssistantStreamService._drain runs against a fake `open_stream`, which is
    the call ARCH-39 restores; `_shrink_plan` is run for a real smaller window.
  * RetrievalService.hybrid_search runs with stubbed search and rerank: the
    résumé-style off-topic document is dropped by the floor.
  * --db writes fixtures inside one transaction and rolls it back: the
    constraints, both triggers, the ORM scope default, session listing,
    export, templates and the free-plan assignment are exercised on the real
    schema.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import types
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
BACKEND = HERE
FRONTEND = HERE.parent / "frontend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

HEAD = "arch39_step1_conversations"
REGRESSIONS = ("verify_arch36.py",)

BACKEND_FILES = (
    "app/services/rag_guard.py",
    "app/services/assistant_stream.py",
    "app/services/llm_service.py",
    "app/services/retrieval_service.py",
    "app/services/conversation_service.py",
    "app/services/stream_session.py",
    "app/services/context_budget.py",
    "app/services/assistant_service.py",
    "app/api/v1/billing.py",
    "app/api/v1/assistant_sessions.py",
    "app/services/billing/portal_service.py",
    "app/models/assistant.py",
    "app/models/assistant_suite.py",
    "alembic/versions/arch39_step1_conversations.py",
)

FRONTEND_FILES = (
    "src/pages/Notifications/Notifications.tsx",
    "src/components/assistant/ChatBubble.tsx",
    "src/components/assistant/ChatPanel.tsx",
    "src/pages/Assistant/Assistant.tsx",
    "src/pages/billing/PlanSelector.tsx",
    "src/services/api/endpoints.ts",
    "src/services/api/assistantSessions.ts",
    "src/components/assistant/ConversationSidebar.tsx",
    "src/components/assistant/ChatSessionBar.tsx",
)

TOUCHED_FRONTEND = FRONTEND_FILES + (
    "src/services/api/queryKeys.ts",
    "src/types/assistant.ts",
    "src/types/assistantSuite.ts",
    "src/types/billing.ts",
    "src/utils/usageCost.ts",
)

SENTINELS = {
    "verify_arch31_step0.py": "ARCH39-S1:head-widened-31s0",
    "verify_arch35.py": "ARCH39-S1:head-widened-35",
    "verify_arch36.py": "ARCH39-S1:head-widened-36",
    "app/core/config.py": "ARCH39-S1:settings",
    "app/models/assistant.py": "ARCH39-S1:conversation-model",
    "app/models/__init__.py": "ARCH39-S1:models-registered",
    "app/api/v1/router.py": "ARCH39-S1:sessions-router",
    "app/schemas/assistant.py": "ARCH39-S1:token-usage",
    "app/schemas/usage.py": "ARCH39-S1:plan-list",
    "app/services/retrieval_service.py": "ARCH39-S1:rerank-floor",
    "app/services/llm_service.py": "ARCH39-S1:synth-budget",
    "app/services/assistant_service.py": "ARCH39-S1:service-override",
    "app/services/assistant_stream.py": "ARCH39-S1:stream-open",
    "app/services/context_budget.py": "ARCH39-S1:request-ceiling",
    "app/services/stream_session.py": "ARCH39-S1:settled-cost",
    "app/services/billing/portal_service.py": "ARCH39-S1:free-plan",
    "app/api/v1/billing.py": "ARCH39-S1:free-plan-endpoint",
}

GROQ_413 = (
    "Error code: 413 - {'error': {'message': 'Request too large for model "
    "`openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` on "
    "tokens per minute (TPM): Limit 12000, Requested 14532, please reduce your "
    "message size and try again.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
)


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn: Callable[[], None]) -> bool:
        try:
            fn()
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            return False
        except Exception as exc:  # noqa: BLE001
            self.results.append((name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"))
            return False
        self.results.append((name, True, ""))
        return True

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.results if not ok)

    def report(self, title: str) -> None:
        print(f"\n--- {title} ---")
        for name, ok, detail in self.results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            if not ok and detail:
                for line in detail.splitlines()[:10]:
                    print(f"         {line}")
        print(f"  {len(self.results) - self.failed}/{len(self.results)} passed")


@dataclass(frozen=True)
class Roots:
    backend: Path
    frontend: Path

    def be(self, rel: str) -> str:
        return _read(self.backend / rel)

    def fe(self, rel: str) -> str:
        return _read(self.frontend / rel)


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing file: {path}")
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def _load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _function_source(src: str, name: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"function {name} not found")


def _calls(src: str, name: str) -> int:
    count = 0
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if called == name:
                count += 1
    return count


class _Fake413(Exception):
    status_code = 413


# ===========================================================================
# Offline — rag_guard, loaded from its file
# ===========================================================================


def gates_rag_guard(rec: Recorder, roots: Roots) -> None:
    rg = _load_module(roots.backend / "app/services/rag_guard.py", f"rag_guard_{uuid.uuid4().hex}")

    def budget_subtracts_output() -> None:
        learned = rg.LearnedCeilings()
        budget = rg.prompt_token_budget(
            provider="groq", model="m", context_window=32768, max_output_tokens=2048,
            ceilings={"groq": 12000}, default_ceiling=32768, learned=learned,
        )
        assert budget == 12000 - 2048 - rg.FRAMING_TOKENS, budget
        specific = rg.configured_ceiling("GROQ", "Big", ceilings={"groq": 12000, "groq:big": 30000}, default=1)
        assert specific == 30000, "provider:model must win over provider"
        assert rg.configured_ceiling("gemini", "x", ceilings={"groq": 1}, default=777) == 777

    rec.check("R1 prompt budget = min(window, ceiling) − max_output − framing", budget_subtracts_output)

    def learned_limits() -> None:
        learned = rg.LearnedCeilings(ttl_seconds=10)
        learned.learn("groq", "m", 9000, now=100.0)
        learned.learn("groq", "m", 11000, now=101.0)
        assert learned.get("groq", "m", now=102.0) == 9000, "a learned limit must never grow"
        assert learned.get("GROQ", "M", now=102.0) == 9000, "keys are case-insensitive"
        assert learned.get("groq", "m", now=200.0) is None, "a learned limit must expire"
        budget = rg.prompt_token_budget(
            provider="groq", model="m", context_window=32768, max_output_tokens=1000,
            ceilings={"groq": 12000}, default_ceiling=32768, learned=_with(rg, "groq", "m", 8000),
        )
        assert budget == 8000 - 1000 - rg.FRAMING_TOKENS, budget

    rec.check("R2 limits learned from a provider only ever shrink, and expire", learned_limits)

    def classifies_413() -> None:
        found = rg.classify_request_too_large(_Fake413(GROQ_413))
        assert found and found.limit == 12000 and found.requested == 14532, found
        bare = rg.classify_request_too_large(_Fake413("Payload rejected by upstream"))
        assert bare is not None and bare.limit is None, "a bare HTTP 413 must be recognised"

        class LLMPermanentError(RuntimeError):
            pass

        class HTTPException(Exception):
            pass

        try:
            try:
                try:
                    raise _Fake413(GROQ_413)
                except _Fake413 as inner:
                    raise LLMPermanentError(str(inner)) from inner
            except LLMPermanentError as middle:
                raise HTTPException("The AI request could not be processed as sent.") from middle
        except HTTPException as outer:
            wrapped = rg.classify_request_too_large(outer)
        assert wrapped and wrapped.limit == 12000, "must see through both wrappers"

        openai_style = rg.classify_request_too_large(
            RuntimeError("This model's maximum context length is 8192 tokens; reduce the length")
        )
        assert openai_style and openai_style.limit == 8192

        class RateLimited(Exception):
            status_code = 429

        assert rg.classify_request_too_large(RateLimited("Rate limit reached, retry in 2s")) is None
        assert rg.classify_request_too_large(ValueError("boom")) is None

    rec.check("R3 a Groq 413 is recognised through both chat paths' wrappers; a plain 429 is not", classifies_413)

    def shrink_never_grows() -> None:
        too_large = rg.RequestTooLarge(limit=12000, requested=14532, message="")
        smaller = rg.shrunk_budget(9888, too_large, max_output_tokens=2048)
        assert smaller < 9888 and smaller <= 12000 - 2048 - rg.FRAMING_TOKENS, smaller
        blind = rg.shrunk_budget(5000, rg.RequestTooLarge(None, None, ""), max_output_tokens=0)
        assert blind == int(5000 * rg.SHRINK_FACTOR)

    rec.check("R4 the retry budget is smaller and fits the named limit", shrink_never_grows)

    def fits_prompt() -> None:
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 400}
            for i in range(200)
        ]
        context = ("Paragraph of invoice context. " * 30 + "\n\n") * 60
        fitted = rg.fit_prompt_parts(
            fixed_text="template " * 100, context=context, history=history, token_budget=4000
        )
        total = (
            rg.estimate_tokens("template " * 100)
            + rg.estimate_tokens(fitted.context)
            + sum(rg.estimate_tokens(m["content"]) + 4 for m in fitted.history)
        )
        assert total <= 4000, f"fitted prompt estimates {total} > 4000"
        assert fitted.context_truncated and fitted.turns_dropped > 0
        assert fitted.history and fitted.history[-1]["content"].startswith("turn 199"), (
            "the newest turn must be kept"
        )
        small = rg.fit_prompt_parts(fixed_text="t", context="short", history=history[:2], token_budget=4000)
        assert not small.context_truncated and small.turns_dropped == 0

    rec.check("R5 context and history are fitted to the budget, newest turns kept", fits_prompt)

    def retrieval_guards() -> None:
        results = [
            {"rerank_score": 6.0, "metadata": {"work_item_id": "inv"}},
            {"rerank_score": -9.5, "metadata": {"work_item_id": "resume"}},
        ]
        kept, dropped = rg.apply_rerank_floor(results, floor=-5.0)
        assert dropped == 1 and kept[0]["metadata"]["work_item_id"] == "inv"
        degraded = [{"rerank_score": None}, {"rerank_score": 3.0}]
        same, zero = rg.apply_rerank_floor(degraded, floor=-5.0)
        assert zero == 0 and len(same) == 2, "a partial or degraded rerank must not be floored"

        dominant = [
            {"retrieval_confidence": 0.9, "metadata": {"work_item_id": "a"}},
            {"retrieval_confidence": 0.4, "metadata": {"work_item_id": "b"}},
        ]
        close = [
            {"retrieval_confidence": 0.9, "metadata": {"work_item_id": "a"}},
            {"retrieval_confidence": 0.8, "metadata": {"work_item_id": "b"}},
        ]
        assert rg.dominant_document(dominant, margin=0.25)
        assert not rg.dominant_document(close, margin=0.25)
        assert len(rg.final_cut(list(range(15)), top_k=5, final_results=8)) == 8
        assert rg.cap_prior(1.4, cap=0.2) == 0.2

    rec.check("R6 floor, dominance, prior cap and final cut behave", retrieval_guards)

    def costs() -> None:
        assert rg.cost_from_settlement(None) == (0.0, "unmetered")
        assert rg.cost_from_settlement({"total_cost_micros": 1234, "price_book_version": 3}) == (0.001234, "price_book")
        assert rg.cost_from_settlement({"total_cost_micros": 0, "price_book_version": None})[1] == "unpriced"

    rec.check("R7 cost and its source come from the settlement", costs)


def _with(rg: types.ModuleType, provider: str, model: str, limit: int) -> Any:
    learned = rg.LearnedCeilings()
    learned.learn(provider, model, limit)
    return learned


# ===========================================================================
# Offline — static relationships (all read from `roots`, so mutable)
# ===========================================================================


def gates_static(rec: Recorder, roots: Roots) -> None:
    stream = roots.be("app/services/assistant_stream.py")
    llm = roots.be("app/services/llm_service.py")

    def stream_opens_with_client() -> None:
        drain = _function_source(stream, "_drain")
        assert _calls(drain, "provider_stream") == 0, (
            "_drain calls provider_stream(), which requires an explicit client (ARCH-23)"
        )
        assert _calls(drain, "open_stream") == 1, "_drain must open the stream through open_stream"
        assert "session.close()" in drain

    rec.check("S1 the streaming path opens its stream through open_stream", stream_opens_with_client)

    def budgets_everywhere() -> None:
        synth = _function_source(llm, "synthesize_response")
        assert "prompt_token_budget(" in synth and "_fit_rag_prompt(" in synth, (
            "the non-streaming path must budget its prompt"
        )
        assert "classify_request_too_large(" in synth and "shrunk_budget(" in synth
        assert "_build_rag_prompt(" not in synth, "the unbudgeted prompt builder is back"
        budget = roots.be("app/services/context_budget.py")
        assert "prompt_token_budget(" in _function_source(budget, "build")
        answer = _function_source(stream, "stream_answer")
        assert "classify_request_too_large(" in answer and "_shrink_plan(" in answer
        assert "if saw_any_chunk or attempt" in answer, "retry only before the first token, once"

    rec.check("S2 both chat paths budget against the request ceiling and retry once", budgets_everywhere)

    def cost_is_settled() -> None:
        assert "cost_from_settlement(" in _function_source(llm, "synthesize_response")
        assert "cost_from_settlement(" in roots.be("app/services/stream_session.py")
        bubble = roots.fe("src/components/assistant/ChatBubble.tsx")
        assert "estimated_cost.toFixed" not in bubble and "formatUsageCost(" in bubble

    rec.check("S3 displayed cost comes from the price-book settlement", cost_is_settled)

    def retrieval_wired() -> None:
        search = _function_source(roots.be("app/services/retrieval_service.py"), "hybrid_search")
        for needle in ("apply_rerank_floor(", "dominant_document(", "final_cut("):
            assert needle in search, f"hybrid_search lost {needle}"
        assert search.index("apply_rerank_floor(") < search.index("_estimate_retrieval_confidence("), (
            "the floor must run on raw scores, before normalisation"
        )
        prior = _function_source(roots.be("app/services/retrieval_service.py"), "_apply_metadata_prior")
        assert "min(overlap * 0.35, 1.00)" not in prior and "cap_prior(" in prior

    rec.check("S4 retrieval applies the floor, dominance check, prior cap and final cut", retrieval_wired)

    def scope_is_workspace_bound() -> None:
        service = roots.be("app/services/conversation_service.py")
        set_scope = _function_source(service, "set_scope")
        assert "WorkItem.workspace_id == conversation.workspace_id" in set_scope
        assert "conversation.work_item_id is not None" in set_scope, "document chats must refuse re-scoping"
        getter = _function_source(service, "get_owned")
        assert "Conversation.user_id == user_id" in getter and "Conversation.workspace_id == workspace_id" in getter
        listing = _function_source(service, "list_sessions")
        assert "Conversation.user_id == user_id" in listing
        assert "retrieval_work_item_ids(" in stream and "apply_model_override(" in stream
        svc = roots.be("app/services/assistant_service.py")
        assert "scope_item_ids(" in svc and "apply_model_override(" in svc

    rec.check("S5 sessions are owner-scoped; scope and model reach both chat paths", scope_is_workspace_bound)

    def migration_matches_models() -> None:
        migration = roots.be("alembic/versions/arch39_step1_conversations.py")
        model = roots.be("app/models/assistant.py")
        suite = roots.be("app/models/assistant_suite.py")
        assert 'down_revision = "arch35_step1_calibration"' in migration
        assert f'revision = "{HEAD}"' in migration
        check = "(scope_mode = 'DOCUMENT') = (work_item_id IS NOT NULL)"
        assert check in migration and check in model, "scope/document CHECK differs between migration and model"
        modes = ast.literal_eval(re.search(r"SCOPE_MODES: tuple\[str, \.\.\.\] = (\([^)]*\))", migration).group(1))
        suite_tree = ast.parse(suite)
        constants = {
            t.id: n.value.value
            for n in suite_tree.body
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        model_modes = ()
        for n in suite_tree.body:
            if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "SCOPE_MODES":
                model_modes = tuple(constants[e.id] for e in n.value.elts)
        assert tuple(modes) == ("WORKSPACE", "SELECTED", "DOCUMENT") and set(modes) == set(model_modes)
        for name in ("trg_conversation_scope_same_workspace", "trg_conversation_messages_last_at",
                     "uq_prompt_templates_org_name_live"):
            assert name in migration, f"migration lost {name}"
        assert "ck_prompt_templates_body_length" in migration and "ck_prompt_templates_body_length" in suite

    rec.check("S6 migration and models agree on scope modes and constraints", migration_matches_models)

    def billing_seam() -> None:
        billing = roots.be("app/api/v1/billing.py")
        checkout = _function_source(billing, "create_checkout_session")
        free = checkout.find("is_free_tier(")
        gateway = checkout.find("portal_service.create_checkout_session(")
        assert 0 <= free < gateway, "the free-plan branch must run before any gateway checkout"
        assert '"PAID_SUBSCRIPTION_ACTIVE"' in checkout and '"BILLING_GATEWAY_NOT_CONFIGURED"' in checkout
        assert 'kind="assigned"' in checkout
        plans = _function_source(billing, "list_plans") if "def list_plans" in billing else billing
        assert "checkout_available=" in plans and "assigned_tier_key(" in plans
        portal = roots.be("app/services/billing/portal_service.py")
        create = _function_source(portal, "create_checkout_session")
        assert create.index("gateway_readiness(") < create.index("_gateway_checkout("), (
            "readiness must be checked before the gateway is called"
        )
        free_fn = _function_source(portal, "select_free_plan")
        assert "LIVE_SUBSCRIPTION_STATUSES" in free_fn and "PaidSubscriptionActiveError" in free_fn
        selector = roots.fe("src/pages/billing/PlanSelector.tsx")
        for needle in ('session.kind === "assigned"', "checkout_available", "Current plan",
                       "organizationBillingReturnPath("):
            assert needle in selector, f"PlanSelector lost {needle}"

    rec.check("S7 Free is assigned before any gateway call; missing keys are explained", billing_seam)

    def notification_links() -> None:
        page = roots.fe("src/pages/Notifications/Notifications.tsx")
        assert "workItemDetailsPath(" in page, "notification links must use workItemDetailsPath"
        assert "/work-items/${" not in page, "a hand-built document link is back"
        assert 'from "@/constants/routes"' not in page

    rec.check("S8 notification document links are tenant-scoped through the helper", notification_links)

    def endpoints_match_router() -> None:
        endpoints = roots.fe("src/services/api/endpoints.ts")
        block = endpoints[endpoints.index("export const ASSISTANT_SESSION_ENDPOINTS"):]
        block = block[: block.index("} as const;")]
        front = {
            re.sub(r"\$\{seg\([^)]*\)\}", "{}", path)
            for path in re.findall(r"/assistant(/[^`]*)`", block)
        }
        router = roots.be("app/api/v1/assistant_sessions.py")
        back = {re.sub(r"\{[^}]+\}", "{}", path) for path in re.findall(r'@router\.\w+\(\s*"([^"]+)"', router)}
        missing = sorted(front - back)
        assert not missing, f"frontend calls paths the router does not serve: {missing}"
        page = roots.fe("src/pages/Assistant/Assistant.tsx")
        assert "<ConversationSidebar" in page and "<ChatSessionBar" in page
        sidebar = roots.fe("src/components/assistant/ConversationSidebar.tsx")
        assert "listSessions(" in sidebar and "getConversations(" not in sidebar

    rec.check("S9 console endpoints match the sessions router; the page uses the new sidebar", endpoints_match_router)


def gates_applied(rec: Recorder) -> None:
    def sentinels() -> None:
        missing = [rel for rel, s in SENTINELS.items() if s not in _read(BACKEND / rel)]
        assert not missing, f"not applied: {missing}. Run python apply_arch39.py"

    rec.check("apply: every backend sentinel is present", sentinels)

    def idempotent() -> None:
        done = subprocess.run(
            [sys.executable, str(BACKEND / "apply_arch39.py"), "--check"],
            cwd=str(BACKEND), capture_output=True, text=True, timeout=120,
        )
        assert done.returncode == 0 and "0 file(s) would change" in done.stdout, done.stdout[-1500:]

    rec.check("apply: a second run would change nothing", idempotent)


# ===========================================================================
# Offline — behaviour through the real modules (needs the backend importable)
# ===========================================================================


def gates_behaviour(rec: Recorder) -> None:
    # The application's import graph has a defined entry point; importing a
    # schema module first trips a circular import that the app never hits.
    import app.main  # noqa: F401
    def synthesize_retries_once() -> None:
        from fastapi import HTTPException

        from app.schemas.ai_settings import AIProvider
        from app.schemas.assistant import TokenUsage
        from app.services import rag_guard
        from app.services.llm_service import LLMService

        rag_guard.learned_ceilings.clear()
        service = LLMService()
        settings_obj = types.SimpleNamespace(
            provider=AIProvider.GROQ, model="arch39-gate-model", temperature=0.2,
            max_output_tokens=2048, top_p=1.0, frequency_penalty=0.0, presence_penalty=0.0,
        )
        prompts: list[str] = []

        def fake_execute(*, prompt: str, temperature: float, ai_settings: Any, byok_client: Any = None):
            prompts.append(prompt)
            if len(prompts) == 1:
                try:
                    raise _Fake413(GROQ_413)
                except _Fake413 as exc:
                    raise HTTPException(status_code=400, detail="could not be processed as sent") from exc
            return "answer", TokenUsage(
                provider="groq", model=ai_settings.model, prompt_tokens=10,
                completion_tokens=5, total_tokens=15, estimated_cost=0.0,
            )

        service._execute_query = fake_execute  # type: ignore[method-assign]
        history = [{"role": "user", "content": "q " * 300} for _ in range(200)]
        context = "Invoice total 1,250.00 due 30 days. " * 900
        text, usage = service.synthesize_response(
            query="What is the invoice total?", context=context, history=history,
            ai_settings=settings_obj,
        )
        assert text == "answer" and len(prompts) == 2, f"{len(prompts)} provider calls"
        assert len(prompts[1]) < len(prompts[0]), "the retry must send a smaller prompt"
        assert rag_guard.estimate_tokens(prompts[0]) <= 12000, (
            "even the first request must fit the configured ceiling"
        )
        assert usage.context_trimmed and usage.cost_source == "unmetered"
        assert rag_guard.learned_ceilings.get("groq", "arch39-gate-model") == 12000

        prompts.clear()

        def always_too_large(**kwargs: Any):
            prompts.append(kwargs["prompt"])
            raise _Fake413(GROQ_413)

        service._execute_query = always_too_large  # type: ignore[method-assign]
        try:
            service.synthesize_response(query="q", context=context, history=history, ai_settings=settings_obj)
        except HTTPException as exc:
            assert exc.status_code == 413 and len(prompts) == 2, (exc.status_code, len(prompts))
        else:
            raise AssertionError("a second refusal must surface as 413, not loop")
        rag_guard.learned_ceilings.clear()

    rec.check("B1 non-streaming chat: bounded prompt, one smaller retry, then a clear 413", synthesize_retries_once)

    def drain_uses_open_stream() -> None:
        import asyncio

        from app.services import llm_stream
        from app.services.assistant_stream import AssistantStreamService, StreamPlan
        from app.services.fenced_context import empty_fence
        from app.services.llm_stream import StreamChunk

        calls: list[dict[str, Any]] = []
        original = llm_stream.open_stream

        def fake_open_stream(db: Any, **kwargs: Any):
            calls.append(kwargs)
            return types.SimpleNamespace(
                chunks=iter([StreamChunk(text="hel"), StreamChunk(text="lo", finish_reason="stop")]),
                credential_use="platform",
            )

        attached: list[Any] = []
        plan = StreamPlan(
            conversation=types.SimpleNamespace(id=uuid.uuid4()),
            organization_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
            ai_settings=types.SimpleNamespace(temperature=0.1, provider=types.SimpleNamespace(value="GROQ"),
                                              model="m", max_output_tokens=512),
            prompt="p" * 40_000, fenced=empty_fence(), results=[],
            reservation=types.SimpleNamespace(attach_credential_use=attached.append),
            message_id=uuid.uuid4(), context_hash=None, audit_log_id=None,
            passages_dropped_budget=0, budget_warnings=[], query_text="q",
            system_prompt="system", history_full=[{"role": "user", "content": "x" * 4000}] * 20,
            window_tokens=9000,
        )
        llm_stream.open_stream = fake_open_stream
        try:
            async def collect() -> list[str]:
                return [chunk.text async for chunk in AssistantStreamService()._drain(plan)]

            texts = asyncio.run(collect())
        finally:
            llm_stream.open_stream = original
        assert texts == ["hel", "lo"], texts
        assert calls and calls[0]["organization_id"] == plan.organization_id
        assert attached == ["platform"], "the reservation must record whose key served the stream"

        from app.services import rag_guard

        from app.services import assistant_stream as stream_module

        sealed: list[Any] = []
        original_seal = stream_module.provenance_service.seal_generation

        def fake_seal(db: Any, **kwargs: Any):
            sealed.append(kwargs["fenced"])
            return "sha256:gate", None

        rag_guard.learned_ceilings.clear()
        stream_module.provenance_service.seal_generation = fake_seal
        try:
            shrunk = AssistantStreamService()._shrink_plan(
                plan, rag_guard.RequestTooLarge(limit=12000, requested=14532, message="")
            )
        finally:
            stream_module.provenance_service.seal_generation = original_seal
        if sealed:
            assert shrunk.context_hash == "sha256:gate", "the retried context must be resealed"
        assert shrunk.context_trimmed and shrunk.window_tokens < plan.window_tokens
        assert len(shrunk.prompt) < len(plan.prompt)
        assert rag_guard.learned_ceilings.get("GROQ", "m") == 12000
        rag_guard.learned_ceilings.clear()

    rec.check("B2 streaming chat: _drain streams via open_stream; _shrink_plan shrinks", drain_uses_open_stream)

    def retrieval_drops_off_topic() -> None:
        import importlib

        # `app.services.retrieval_service` the attribute is the service
        # instance; the module is what holds the collaborators to stub.
        module = importlib.import_module("app.services.retrieval_service")

        saved = {
            name: getattr(module, name)
            for name in ("intent_service", "hybrid_search_service", "reranker_client", "document_filter_service")
        }

        def result(doc: str, idx: int, score: float) -> dict[str, Any]:
            return {
                "id": f"{doc}-{idx}", "similarity_score": 0.4, "lexical_score": 0.1,
                "rerank_score": score, "rerank_status": "ok",
                "metadata": {"work_item_id": doc, "original_filename": f"{doc}.pdf"},
            }

        candidates = [result("invoice", i, 7.0 - i * 0.1) for i in range(10)]
        candidates += [result("resume", i, -9.0) for i in range(5)]
        module.intent_service = types.SimpleNamespace(
            detect=lambda *a, **k: types.SimpleNamespace(confident=False, intent=None)
        )
        module.hybrid_search_service = types.SimpleNamespace(
            search=lambda *a, **k: types.SimpleNamespace(results=[dict(c) for c in candidates])
        )
        module.reranker_client = types.SimpleNamespace(rerank=lambda **k: k["results"])
        module.document_filter_service = types.SimpleNamespace(filter_documents=lambda r: r)
        try:
            out = module.RetrievalService().hybrid_search(
                workspace_id=uuid.uuid4(), query="invoice details", top_k=5,
                similarity_threshold=0.2, db=object(),
            )
        finally:
            for name, value in saved.items():
                setattr(module, name, value)
        docs = {r["metadata"]["work_item_id"] for r in out}
        assert docs == {"invoice"}, f"off-topic document survived: {docs}"
        from app.core.config import settings

        assert len(out) <= max(5, settings.RERANK_FINAL_RESULTS), len(out)

    rec.check("B3 retrieval: an off-topic document below the floor is not cited", retrieval_drops_off_topic)

    def readiness_explains() -> None:
        from pydantic import SecretStr

        from app.core.config import settings
        from app.services.billing import portal_service

        saved = (settings.BILLING_GATEWAY, settings.DODO_API_KEY, settings.ENVIRONMENT)
        try:
            settings.BILLING_GATEWAY = "DODO"
            settings.DODO_API_KEY = None
            settings.ENVIRONMENT = "development"
            reason = portal_service.gateway_readiness()
            assert reason and "DODO_API_KEY" in reason and "Free plan" in reason, reason
            settings.ENVIRONMENT = "production"
            assert "DODO_API_KEY" not in (portal_service.gateway_readiness() or ""), (
                "production messages must not name configuration"
            )
            settings.DODO_API_KEY = SecretStr("test_key")
            assert portal_service.gateway_readiness() is None
        finally:
            settings.BILLING_GATEWAY, settings.DODO_API_KEY, settings.ENVIRONMENT = saved

    rec.check("B4 a missing gateway key is reported (named only outside production)", readiness_explains)


# ===========================================================================
# Build
# ===========================================================================


def _npx() -> str:
    return "npx.cmd" if os.name == "nt" else "npx"


def gates_build(rec: Recorder) -> None:
    def run(command: list[str]) -> None:
        done = subprocess.run(command, cwd=str(FRONTEND), capture_output=True, text=True, timeout=900)
        assert done.returncode == 0, (done.stdout + done.stderr)[-2500:]

    if not rec.check("build: node_modules present",
                     lambda: None if (FRONTEND / "node_modules").exists() else (_ for _ in ()).throw(
                         AssertionError("frontend/node_modules missing; run npm install"))):
        return
    rec.check("build: tsc --noEmit", lambda: run([_npx(), "tsc", "--noEmit", "-p", "tsconfig.json"]))
    rec.check("build: eslint on touched files", lambda: run([_npx(), "eslint", *TOUCHED_FRONTEND]))
    rec.check("build: vite build", lambda: run([_npx(), "vite", "build"]))


# ===========================================================================
# Database
# ===========================================================================


def gates_db(rec: Recorder) -> None:
    from sqlalchemy import text as sql
    from sqlalchemy.exc import DBAPIError

    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.models.assistant import Conversation, ConversationMessage
    from app.services import conversation_service
    from app.services.billing import portal_service

    db = SessionLocal()
    tag = uuid.uuid4().hex[:10]
    ids: dict[str, uuid.UUID] = {}

    def expect_refused(statement: str, params: dict[str, Any], label: str) -> None:
        savepoint = db.begin_nested()
        try:
            db.execute(sql(statement), params)
            db.flush()
        except DBAPIError:
            savepoint.rollback()
            return
        savepoint.rollback()
        raise AssertionError(f"the database accepted {label}")

    try:
        def head() -> None:
            value = db.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
            # ARCH37-S1:head-widened-39. ARCH-37 moves the head forward.
            # ARCH38-S1:head-widened-39
            assert value in (HEAD, "arch37_step1_flow_builder", "arch38_step1_batches", "arch40_step2_settings_backfill", "arch40_step2a_review_view_paths", "arch40_step3_contract_ai_settings", "hm1_tier_price_per_key", "arch41_step1_extraction_memory", "arch42_step1_entity_graph"), f"alembic head is {value}; run `alembic upgrade head`"  # ARCH40-S1:head-widened-39  # HM-S1:head-widened  ARCH41-S2:head-widened-39  ARCH42-S1:head-widened-39

        if not rec.check(f"DB: head is {HEAD}", head):
            return

        def fixtures() -> None:
            for key in ("user", "org", "ws", "ws2", "doc", "doc2", "other_doc"):
                ids[key] = uuid.uuid4()
            db.execute(sql(
                "INSERT INTO users (id, email, hashed_password, is_active, is_superuser, timezone, locale) "
                "VALUES (:id, :email, 'x', true, false, 'UTC', 'en')"),
                {"id": ids["user"], "email": f"arch39-{tag}@example.test"})
            db.execute(sql(
                "INSERT INTO organizations (id, slug, name, status) VALUES (:id, :slug, 'ARCH-39 gate', 'ACTIVE')"),
                {"id": ids["org"], "slug": f"arch39-{tag}"})
            for key in ("ws", "ws2"):
                db.execute(sql(
                    "INSERT INTO workspaces (id, workspace_name, timezone, language, currency, date_format, "
                    "organization_id, slug, status) VALUES (:id, :name, 'UTC', 'en', 'USD', 'YYYY-MM-DD', "
                    ":org, :slug, 'ACTIVE')"),
                    {"id": ids[key], "name": f"gate {key}", "org": ids["org"], "slug": f"{key}-{tag}"})
            for key, ws in (("doc", "ws"), ("doc2", "ws"), ("other_doc", "ws2")):
                db.execute(sql(
                    "INSERT INTO work_items (id, original_filename, stored_filename, file_type, file_size, "
                    "status, workspace_id) VALUES (:id, :name, :name, 'application/pdf', 10, 'COMPLETED', :ws)"),
                    {"id": ids[key], "name": f"{key}-{tag}.pdf", "ws": ids[ws]})
            db.flush()

        if not rec.check("DB: fixtures insert (rolled back at the end)", fixtures):
            return

        def orm_default_and_check() -> None:
            doc_chat = Conversation(title="Doc chat", workspace_id=ids["ws"], user_id=ids["user"],
                                    work_item_id=ids["doc"])
            ws_chat = Conversation(title="Invoices Q3", workspace_id=ids["ws"], user_id=ids["user"])
            db.add_all([doc_chat, ws_chat])
            db.flush()
            db.refresh(doc_chat)
            db.refresh(ws_chat)
            assert doc_chat.scope_mode == "DOCUMENT" and ws_chat.scope_mode == "WORKSPACE"
            ids["doc_chat"], ids["ws_chat"] = doc_chat.id, ws_chat.id
            expect_refused("UPDATE conversations SET scope_mode='WORKSPACE' WHERE id=:id",
                           {"id": doc_chat.id}, "widening a document chat")
            expect_refused("UPDATE conversations SET scope_mode='SELECTED', work_item_id=:doc WHERE id=:id",
                           {"id": ws_chat.id, "doc": ids["doc"]}, "a SELECTED chat bound to a document")
            expect_refused("UPDATE conversations SET scope_mode='EVERYTHING' WHERE id=:id",
                           {"id": ws_chat.id}, "an unknown scope mode")

        rec.check("DB: ORM derives DOCUMENT scope; CHECKs refuse widening and unknown modes", orm_default_and_check)

        def scope_trigger() -> None:
            expect_refused(
                "INSERT INTO conversation_scope_items (conversation_id, work_item_id, workspace_id) "
                "VALUES (:c, :w, :ws)",
                {"c": ids["ws_chat"], "w": ids["other_doc"], "ws": ids["ws"]},
                "a scope row for another workspace's document",
            )
            expect_refused(
                "INSERT INTO conversation_scope_items (conversation_id, work_item_id, workspace_id) "
                "VALUES (:c, :w, :ws)",
                {"c": ids["doc_chat"], "w": ids["doc2"], "ws": ids["ws"]},
                "a scope row on a document conversation",
            )
            chat = db.get(Conversation, ids["ws_chat"])
            try:
                conversation_service.set_scope(db, conversation=chat, mode="SELECTED",
                                               work_item_ids=[ids["other_doc"]])
            except conversation_service.ScopeError:
                pass
            else:
                raise AssertionError("set_scope accepted another workspace's document")
            chosen = conversation_service.set_scope(db, conversation=chat, mode="SELECTED",
                                                    work_item_ids=[ids["doc"], ids["doc2"], ids["doc"]])
            assert chosen == [ids["doc"], ids["doc2"]] and chat.scope_mode == "SELECTED"
            assert set(conversation_service.retrieval_work_item_ids(db, conversation=chat)) == {
                str(ids["doc"]), str(ids["doc2"])}
            doc_chat = db.get(Conversation, ids["doc_chat"])
            assert conversation_service.retrieval_work_item_ids(db, conversation=doc_chat) == [str(ids["doc"])]
            try:
                conversation_service.set_scope(db, conversation=doc_chat, mode="WORKSPACE")
            except conversation_service.ScopeError:
                pass
            else:
                raise AssertionError("a document conversation was re-scoped")

        rec.check("DB: scope trigger and service refuse cross-workspace and document re-scoping", scope_trigger)

        def last_message_and_listing() -> None:
            chat = db.get(Conversation, ids["ws_chat"])
            db.add(ConversationMessage(conversation_id=chat.id, role="user",
                                       content=f"What is the total on invoice {tag}?"))
            db.flush()
            db.refresh(chat)
            assert chat.last_message_at is not None, "trg_conversation_messages_last_at did not fire"
            conversation_service.update_session(db, conversation=db.get(Conversation, ids["doc_chat"]),
                                                ai_settings=None, pinned=True)
            rows = conversation_service.list_sessions(db, workspace_id=ids["ws"], user_id=ids["user"])
            assert [r.conversation.id for r in rows][0] == ids["doc_chat"], "pinned must sort first"
            docs = conversation_service.list_sessions(db, workspace_id=ids["ws"], user_id=ids["user"], kind="document")
            assert [r.conversation.id for r in docs] == [ids["doc_chat"]]
            found = conversation_service.list_sessions(db, workspace_id=ids["ws"], user_id=ids["user"], query=tag)
            assert ids["ws_chat"] in {r.conversation.id for r in found}, "message-text search failed"
            stranger = conversation_service.list_sessions(db, workspace_id=ids["ws"], user_id=uuid.uuid4())
            assert stranger == [], "another user saw these conversations"
            conversation_service.update_session(db, conversation=chat, ai_settings=None, archived=True)
            live = {r.conversation.id for r in conversation_service.list_sessions(
                db, workspace_id=ids["ws"], user_id=ids["user"])}
            assert ids["ws_chat"] not in live
            body, media, name = conversation_service.export_conversation(db, conversation=chat, fmt="markdown")
            assert tag in body and media.startswith("text/markdown") and name.endswith(".md")

        rec.check("DB: last_message_at trigger, pinned order, kind/search/owner filters, archive, export",
                  last_message_and_listing)

        def model_override() -> None:
            from app.core.ai_models import AI_MODELS
            from app.schemas.ai_settings import AIProvider

            chat = db.get(Conversation, ids["doc_chat"])
            ai = types.SimpleNamespace(provider=AIProvider.GROQ, model=AI_MODELS[AIProvider.GROQ][0])
            other = AI_MODELS[AIProvider.GROQ][1]
            conversation_service.update_session(db, conversation=chat, ai_settings=ai, model_override=other)
            assert chat.model_override == other
            assert conversation_service.apply_model_override(ai, chat).model == other
            assert ai.model == AI_MODELS[AIProvider.GROQ][0], "the workspace settings row was mutated"
            try:
                conversation_service.update_session(db, conversation=chat, ai_settings=ai,
                                                    model_override="gemini-2.5-pro")
            except conversation_service.ModelOverrideError:
                pass
            else:
                raise AssertionError("a model from another provider was accepted")

        rec.check("DB: model override is limited to the provider's models and never mutates settings",
                  model_override)

        def templates() -> None:
            first = conversation_service.create_template(db, organization_id=ids["org"], user_id=ids["user"],
                                                         name="Payment terms", body="List payment terms.")
            try:
                conversation_service.create_template(db, organization_id=ids["org"], user_id=ids["user"],
                                                     name="PAYMENT  terms", body="dup")
            except conversation_service.PromptTemplateError:
                pass
            else:
                raise AssertionError("a case-insensitive duplicate name was accepted")
            expect_refused(
                "INSERT INTO prompt_templates (id, organization_id, name, body) VALUES (:id, :org, 'payment terms', 'x')",
                {"id": uuid.uuid4(), "org": ids["org"]}, "a duplicate live template name")
            conversation_service.archive_template(db, template=first)
            conversation_service.create_template(db, organization_id=ids["org"], user_id=ids["user"],
                                                 name="Payment terms", body="Second version.")
            names = [t.body for t in conversation_service.list_templates(db, organization_id=ids["org"])]
            assert names == ["Second version."], names

        rec.check("DB: template names are unique per organization among live templates", templates)

        def free_plan() -> None:
            settings.QUOTA_TIER_CACHE_TTL_SECONDS = 0
            tier_key = f"g39free{tag}"[:32]
            tier_id = uuid.uuid4()
            db.execute(sql(
                "INSERT INTO quota_tiers (id, key, display_name, version, effective_from, published_at, is_active, "
                "unit_amount_micros, currency, billing_interval) VALUES (:id, :key, 'Gate Free', 1, "
                "now() - interval '1 day', now() - interval '1 day', true, 0, 'USD', 'month')"),
                {"id": tier_id, "key": tier_key})
            db.flush()
            assert portal_service.is_free_tier(db, quota_tier_key=tier_key)
            portal_service.select_free_plan(db, organization_id=ids["org"], quota_tier_key=tier_key)
            assigned = db.execute(sql("SELECT quota_tier_id FROM organizations WHERE id=:id"),
                                  {"id": ids["org"]}).scalar_one()
            assert assigned == tier_id
            assert portal_service.assigned_tier_key(db, organization_id=ids["org"]) == tier_key

            book = uuid.uuid4()
            account = uuid.uuid4()
            db.execute(sql("INSERT INTO price_books (id, version, effective_from) VALUES (:id, 900000 + floor(random()*90000)::int, now())"),
                       {"id": book})
            db.execute(sql("INSERT INTO billing_accounts (id, organization_id, billing_email, gateway, gateway_customer_id) "
                           "VALUES (:id, :org, :email, 'DODO', 'cus_gate')"),
                       {"id": account, "org": ids["org"], "email": f"billing-{tag}@example.test"})
            db.execute(sql(
                "INSERT INTO subscriptions (id, billing_account_id, status, quota_tier_key, quota_tier_id, "
                "price_book_id, current_period_start, current_period_end, gateway, gateway_subscription_id) "
                "VALUES (:id, :acct, 'active', :key, :tier, :book, now(), now() + interval '30 days', 'DODO', 'sub_gate')"),
                {"id": uuid.uuid4(), "acct": account, "key": tier_key, "tier": tier_id, "book": book})
            db.flush()
            try:
                portal_service.select_free_plan(db, organization_id=ids["org"], quota_tier_key=tier_key)
            except portal_service.PaidSubscriptionActiveError:
                pass
            else:
                raise AssertionError("Free was assigned under a live paid subscription")

        rec.check("DB: Free is assigned without a gateway; refused under a live subscription", free_plan)
    finally:
        db.rollback()
        db.close()


# ===========================================================================
# Mutation
# ===========================================================================

MUTANTS: tuple[dict[str, str], ...] = (
    {"id": "M01", "name": "budget forgets max_output", "must": "die",
     "file": "backend/app/services/rag_guard.py",
     "find": "limit - max(0, int(max_output_tokens)) - FRAMING_TOKENS)",
     "replace": "limit - FRAMING_TOKENS)"},
    {"id": "M02", "name": "413 status ignored", "must": "die",
     "file": "backend/app/services/rag_guard.py",
     "find": "is_413 = status == 413", "replace": "is_413 = False"},
    {"id": "M03", "name": "oldest history kept instead of newest", "must": "die",
     "file": "backend/app/services/rag_guard.py",
     "find": "for message in reversed(list(history)):", "replace": "for message in list(history):"},
    {"id": "M04", "name": "floor applied over missing scores", "must": "die",
     "file": "backend/app/services/rag_guard.py",
     "find": "    if any(score is None for score in scores):\n        return items, 0\n", "replace": ""},
    {"id": "M05", "name": "learned limit may grow", "must": "die",
     "file": "backend/app/services/rag_guard.py",
     "find": "limit = min(limit, current[0])", "replace": "limit = max(limit, current[0])"},
    {"id": "M06", "name": "stream reverts to provider_stream()", "must": "die",
     "file": "backend/app/services/assistant_stream.py",
     "find": "                for chunk in opened.chunks:",
     "replace": "                for chunk in provider_stream(prompt=plan.prompt, temperature=0.0, ai_settings=plan.ai_settings):"},
    {"id": "M07", "name": "non-streaming cost literal restored", "must": "die",
     "file": "backend/app/services/llm_service.py",
     "find": "cost, cost_source = rag_guard.cost_from_settlement(settlement)",
     "replace": "cost, cost_source = 0.0, None"},
    {"id": "M08", "name": "rerank floor removed", "must": "die",
     "file": "backend/app/services/retrieval_service.py",
     "find": "merged_results, below_floor = rag_guard.apply_rerank_floor(",
     "replace": "merged_results, below_floor = (lambda r, floor: (r, 0))("},
    {"id": "M09", "name": "free branch removed from checkout", "must": "die",
     "file": "backend/app/api/v1/billing.py",
     "find": "if not payload.price_id and portal_service.is_free_tier(",
     "replace": "if False and portal_service.is_free_tier_disabled("},
    {"id": "M10", "name": "notification link hand-built again", "must": "die",
     "file": "frontend/src/pages/Notifications/Notifications.tsx",
     "find": "? workItemDetailsPath(",
     "replace": "? `/${tenantState.organization.organization_slug}/work-items/${alert.work_item_id}` && workItemDetailsPath("},
    {"id": "M11", "name": "migration drops the document CHECK", "must": "die",
     "file": "backend/alembic/versions/arch39_step1_conversations.py",
     "find": "\"(scope_mode = 'DOCUMENT') = (work_item_id IS NOT NULL)\",",
     "replace": "\"scope_mode IS NOT NULL\","},
    {"id": "M12", "name": "bubble prints the literal cost", "must": "die",
     "file": "frontend/src/components/assistant/ChatBubble.tsx",
     "find": "{formatUsageCost(message.token_usage)}",
     "replace": "${message.token_usage.estimated_cost.toFixed(4)}"},
    {"id": "M13", "name": "console calls a path the router lacks", "must": "die",
     "file": "frontend/src/services/api/endpoints.ts",
     "find": "`${scoped(workspaceId)}/assistant/models`",
     "replace": "`${scoped(workspaceId)}/assistant/model-list`"},
    {"id": "M14", "name": "set_scope forgets the workspace", "must": "die",
     "file": "backend/app/services/conversation_service.py",
     "find": "                WorkItem.workspace_id == conversation.workspace_id,\n                WorkItem.id.in_(unique_ids),",
     "replace": "                WorkItem.id.in_(unique_ids),"},
    {"id": "M15", "name": "readiness checked after the gateway call", "must": "die",
     "file": "backend/app/services/billing/portal_service.py",
     "find": "    not_ready = gateway_readiness(gateway_name)\n    if not_ready is not None:\n        raise CheckoutGatewayUnavailableError(not_ready, gateway=gateway_name)\n",
     "replace": ""},
    {"id": "M16", "name": "retry allowed after tokens were streamed", "must": "die",
     "file": "backend/app/services/assistant_stream.py",
     "find": "if saw_any_chunk or attempt", "replace": "if attempt"},
    {"id": "M17", "name": "comment-only change in rag_guard", "must": "survive",
     "file": "backend/app/services/rag_guard.py",
     "find": "PURE BY DESIGN", "replace": "PURE BY DESIGN (edited)"},
)


def _mutant_roots(tmp: Path) -> Roots:
    be = tmp / "backend"
    fe = tmp / "frontend"
    for rel in BACKEND_FILES:
        (be / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BACKEND / rel, be / rel)
    for rel in ("app/services/stream_session.py", "app/services/context_budget.py"):
        shutil.copyfile(BACKEND / rel, be / rel)
    for rel in FRONTEND_FILES:
        (fe / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FRONTEND / rel, fe / rel)
    return Roots(backend=be, frontend=fe)


def run_mutations(rec: Recorder) -> None:
    for mutant in MUTANTS:
        def gate(mutant: dict[str, str] = mutant) -> None:
            with tempfile.TemporaryDirectory() as tmp:
                roots = _mutant_roots(Path(tmp))
                target = Path(tmp) / mutant["file"]
                text = _read(target)
                assert mutant["find"] in text, f"{mutant['id']}: anchor not found"
                target.write_text(text.replace(mutant["find"], mutant["replace"], 1), encoding="utf-8")
                sub = Recorder()
                gates_rag_guard(sub, roots)
                gates_static(sub, roots)
                died = sub.failed > 0
                if mutant["must"] == "die":
                    assert died, f"{mutant['id']} SURVIVED: {mutant['name']}"
                else:
                    assert not died, f"{mutant['id']} DIED: {[n for n, ok, _ in sub.results if not ok]}"

        verb = "DIES" if mutant["must"] == "die" else "SURVIVES"
        rec.check(f"{mutant['id']} {mutant['name']} -> {verb}", gate)


def run_regressions(rec: Recorder, *, db: bool) -> None:
    for script in REGRESSIONS:
        # In backend/verify_arch39.py around line 1098-1103:
        def gate(script: str = script) -> None:
            command = [sys.executable, str(BACKEND / script)] + (["--db"] if db else [])
            done = subprocess.run(
                command,
                cwd=str(BACKEND),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
            )
            lines = (done.stdout or "").splitlines()
            print(f"         ({script}) {lines[-1] if lines else ''}")
            assert done.returncode == 0, "\n".join(lines[-30:])

        rec.check(f"regression: {script}{' --db' if db else ''}", gate)


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-39 verification")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--skip-regressions", action="store_true")
    args = parser.parse_args()

    print("ARCH-39 — Conversational AI Suite & Production RAG Hardening")
    print(f"backend:  {BACKEND}\nfrontend: {FRONTEND}")
    if not (FRONTEND / "src").is_dir():
        print("frontend/src not found; run from backend/")
        return 2

    roots = Roots(backend=BACKEND, frontend=FRONTEND)
    failed = 0

    offline = Recorder()
    gates_applied(offline)
    gates_rag_guard(offline, roots)
    gates_static(offline, roots)
    try:
        gates_behaviour(offline)
    except Exception:  # noqa: BLE001
        offline.results.append(("behaviour harness", False, traceback.format_exc()))
    offline.report("offline")
    failed += offline.failed

    if args.build:
        build = Recorder()
        gates_build(build)
        build.report("build")
        failed += build.failed

    if args.db:
        live = Recorder()
        try:
            gates_db(live)
        except Exception:  # noqa: BLE001
            live.results.append(("database harness", False, traceback.format_exc()))
        live.report("database")
        failed += live.failed

    if args.mutate:
        mutate = Recorder()
        run_mutations(mutate)
        mutate.report("mutation")
        failed += mutate.failed

    if not args.skip_regressions:
        regression = Recorder()
        run_regressions(regression, db=args.db)
        regression.report("regression")
        failed += regression.failed

    print(f"\n{'FAILED' if failed else 'PASSED'} — {failed} gate(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())