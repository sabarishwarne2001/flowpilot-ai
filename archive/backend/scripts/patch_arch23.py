#!/usr/bin/env python
"""ARCH-23 — anchored edits to the two files that still hold legacy Gemini.

WHY A PATCH SCRIPT

`llm_service.py` is 880 lines of provider gateway, retry policy, prompt
construction and enrichment. `ai_settings_service.py` is a settings service
with one three-line probe that matters. Retyping either to change forty lines
is how a transcription error enters a system whose tests do not cover the path
being changed. Precedent: ARCH-19 shipped 46 surgical changes this way, and
ARCH-0V shipped 23.

CONTRACT (all asserted, not assumed)

  1. IDEMPOTENT — running twice changes nothing the second time.
  2. LOUD ON ANCHOR MISS — an anchor that does not appear exactly once is a
     hard failure naming the file and the anchor. Never guesses, never fuzzy-
     matches, never partially applies a file.
  3. ATOMIC PER FILE — edits stage in memory and write once.

  usage:  python scripts/patch_arch23.py --check
          python scripts/patch_arch23.py --apply
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent


class AnchorMiss(Exception):
    """An anchor did not appear exactly once."""


@dataclass
class Edit:
    edit_id: str
    anchor: str
    replacement: str
    rationale: str
    #: Text whose presence means this edit is already applied. Must appear in
    #: `replacement` — ARCH-0V edit 0V-6.5 shipped a marker that did not, and
    #: it applied once then failed loudly on every rerun.
    applied_marker: str

    def apply(self, source: str, *, path: str) -> tuple[str, str]:
        if self.applied_marker in source:
            return source, "SKIP"

        occurrences = source.count(self.anchor)
        if occurrences != 1:
            raise AnchorMiss(
                f"{path}: edit {self.edit_id} expected its anchor exactly once, "
                f"found {occurrences}.\n"
                f"        anchor: {self.anchor.strip().splitlines()[0][:88]!r}\n"
                f"        The file has drifted from the ARCH-23 baseline. "
                f"Re-read it before forcing this through."
            )
        return source.replace(self.anchor, self.replacement), "APPLIED"


# =====================================================================
# llm_service.py
# =====================================================================

LLM_SERVICE_EDITS: list[Edit] = [
    Edit(
        edit_id="23-1.1",
        applied_marker="from app.core.byok_providers import (",
        anchor="class _RoutedProvider:\n",
        replacement=(
            "from app.core.byok_providers import (\n"
            "    BYOK_TASK_TYPE_VALUES,\n"
            "    PROVIDER_ANTHROPIC,\n"
            "    PROVIDER_AZURE_OPENAI,\n"
            "    PROVIDER_GEMINI,\n"
            "    PROVIDER_GROQ,\n"
            "    PROVIDER_MISTRAL,\n"
            "    PROVIDER_OPENAI,\n"
            "    ROUTABLE_PROVIDERS,\n"
            "    normalize_provider,\n"
            "    supports_task,\n"
            ")\n"
            "\n"
            "\n"
            "class _RoutedProvider:\n"
        ),
        rationale="The registry becomes the single source of provider truth.",
    ),
    Edit(
        edit_id="23-1.2",
        applied_marker="def gemini_client(self) -> Any:",
        anchor=(
            "    @property\n"
            "    def gemini_model(self) -> Any:\n"
            "        if settings.GEMINI_API_KEY is None:\n"
            "            raise ValueError(\"GEMINI_API_KEY is not configured.\")\n"
            "        import google.generativeai as genai\n"
            "\n"
            "        logger.info(\"Initializing Gemini client.\")\n"
            "        genai.configure(api_key=settings.GEMINI_API_KEY.get_secret_value())\n"
            "        return genai\n"
        ),
        replacement=(
            "    @property\n"
            "    def gemini_client(self) -> Any:\n"
            "        \"\"\"The platform's own Gemini client. ARCH-23.\n"
            "\n"
            "        This replaced a property that called `genai.configure()`, which\n"
            "        wrote the API key into MODULE-GLOBAL state inside\n"
            "        `google.generativeai`. On a worker serving several tenants, the\n"
            "        last caller to configure won — so a tenant key set there was\n"
            "        readable by every other tenant's request. That is the single\n"
            "        reason Gemini was unroutable from ARCH-22 until now.\n"
            "\n"
            "        `google.genai.Client(api_key=...)` binds the key to an instance.\n"
            "        The platform client is still cached, which is correct: it holds\n"
            "        the PLATFORM key, and there is only one of those. Tenant clients\n"
            "        are never cached anywhere — see ProviderClientFactory.\n"
            "        \"\"\"\n"
            "        if settings.GEMINI_API_KEY is None:\n"
            "            raise ValueError(\"GEMINI_API_KEY is not configured.\")\n"
            "        if self._gemini_client is None:\n"
            "            from google import genai\n"
            "\n"
            "            logger.info(\"Initializing platform Gemini client.\")\n"
            "            self._gemini_client = genai.Client(\n"
            "                api_key=settings.GEMINI_API_KEY.get_secret_value()\n"
            "            )\n"
            "        return self._gemini_client\n"
        ),
        rationale="Replace the process-global Gemini SDK with a client object.",
    ),
    Edit(
        edit_id="23-1.3",
        applied_marker="self._gemini_client: Any | None = None",
        anchor=(
            "        self._groq_client: Any | None = None\n"
            "        self._gemini_model: Any | None = None\n"
        ),
        replacement=(
            "        self._groq_client: Any | None = None\n"
            "        self._gemini_client: Any | None = None\n"
        ),
        rationale="Rename the cached attribute to match the new client type.",
    ),
    Edit(
        edit_id="23-1.4",
        applied_marker="ARCH-23: every registered provider is accepted",
        anchor=(
            "    def _validate_provider(self, *, ai_settings: AISettings) -> str:\n"
            "        provider = ai_settings.provider.value.strip().lower()\n"
            "        supported = {\"groq\", \"gemini\"}\n"
            "        if provider not in supported:\n"
            "            raise ValueError(\n"
            "                f\"Unsupported LLM provider '{provider}'. Supported "
            "providers: {sorted(supported)}.\"\n"
            "            )\n"
            "        return provider\n"
        ),
        replacement=(
            "    def _validate_provider(self, *, ai_settings: AISettings) -> str:\n"
            "        \"\"\"The provider name, normalised, or a ValueError.\n"
            "\n"
            "        ARCH-23: every registered provider is accepted, not the two the\n"
            "        `ai_provider` PostgreSQL enum happens to hold. The set is read\n"
            "        from `ROUTABLE_PROVIDERS` rather than written out here, so a\n"
            "        provider becomes executable by flipping one registry flag and\n"
            "        adding an adapter — the two things gate 23-G3 checks agree.\n"
            "\n"
            "        A hardcoded `{\"groq\", \"gemini\"}` was the last place the\n"
            "        execution layer disagreed with the BYOK console. A tenant could\n"
            "        store an OpenAI key, see it validated, save a routing rule, and\n"
            "        have this method reject the call.\n"
            "\n"
            "        Returned lower-case because every downstream comparison in this\n"
            "        module and in `llm_resilience` is lower-case, and changing that\n"
            "        would touch the breaker names and the attempt trail.\n"
            "        \"\"\"\n"
            "        raw = ai_settings.provider.value\n"
            "        try:\n"
            "            provider = normalize_provider(raw)\n"
            "        except Exception as exc:  # noqa: BLE001 — UnknownProviderError\n"
            "            raise ValueError(\n"
            "                f\"Unsupported LLM provider '{raw}'. Known providers: \"\n"
            "                f\"{', '.join(sorted(ROUTABLE_PROVIDERS))}.\"\n"
            "            ) from exc\n"
            "\n"
            "        if provider not in ROUTABLE_PROVIDERS:\n"
            "            raise ValueError(\n"
            "                f\"'{provider}' is a known provider but is not routable, \"\n"
            "                \"so no execution adapter can serve this call.\"\n"
            "            )\n"
            "        return provider.lower()\n"
        ),
        rationale="Widen provider validation from two hardcoded names to the registry.",
    ),
    Edit(
        edit_id="23-1.5",
        applied_marker="def _query_openai_compatible(",
        anchor=(
            "    def _query_gemini(\n"
            "        self,\n"
            "        *,\n"
            "        prompt: str,\n"
            "        temperature: float,\n"
            "        ai_settings: AISettings,\n"
            "    ) -> tuple[str, Any]:\n"
        ),
        replacement=(
            "    def _query_openai_compatible(\n"
            "        self,\n"
            "        *,\n"
            "        prompt: str,\n"
            "        temperature: float,\n"
            "        ai_settings: AISettings,\n"
            "        client: Any,\n"
            "        provider_label: str,\n"
            "    ) -> tuple[str, TokenUsage]:\n"
            "        \"\"\"One completion path for every provider speaking the OpenAI shape.\n"
            "\n"
            "        ARCH-23. Groq, OpenAI, Azure OpenAI and Mistral all expose\n"
            "        `chat.completions.create` with the same request and response\n"
            "        shape. Four near-identical methods would drift, and a drifted\n"
            "        token count is a billing defect rather than a cosmetic one.\n"
            "\n"
            "        `client` is REQUIRED here, unlike `_query_groq`, whose optional\n"
            "        parameter exists for backward compatibility with the platform\n"
            "        path. A default would let a caller silently reach the singleton.\n"
            "        \"\"\"\n"
            "        completion = client.chat.completions.create(\n"
            "            model=ai_settings.model,\n"
            "            temperature=temperature,\n"
            "            top_p=ai_settings.top_p,\n"
            "            frequency_penalty=ai_settings.frequency_penalty,\n"
            "            presence_penalty=ai_settings.presence_penalty,\n"
            "            max_tokens=ai_settings.max_output_tokens,\n"
            "            messages=[{\"role\": \"user\", \"content\": prompt}],\n"
            "        )\n"
            "        usage = completion.usage\n"
            "        return (\n"
            "            str(completion.choices[0].message.content).strip(),\n"
            "            TokenUsage(\n"
            "                provider=provider_label,\n"
            "                model=ai_settings.model,\n"
            "                prompt_tokens=int(getattr(usage, \"prompt_tokens\", 0) or 0),\n"
            "                completion_tokens=int(\n"
            "                    getattr(usage, \"completion_tokens\", 0) or 0\n"
            "                ),\n"
            "                total_tokens=int(getattr(usage, \"total_tokens\", 0) or 0),\n"
            "                estimated_cost=0.0,\n"
            "            ),\n"
            "        )\n"
            "\n"
            "    def _query_anthropic(\n"
            "        self,\n"
            "        *,\n"
            "        prompt: str,\n"
            "        temperature: float,\n"
            "        ai_settings: AISettings,\n"
            "        client: Any,\n"
            "    ) -> tuple[str, TokenUsage]:\n"
            "        \"\"\"Anthropic's messages API. Its own request and usage shape.\"\"\"\n"
            "        message = client.messages.create(\n"
            "            model=ai_settings.model,\n"
            "            max_tokens=ai_settings.max_output_tokens,\n"
            "            temperature=temperature,\n"
            "            top_p=ai_settings.top_p,\n"
            "            messages=[{\"role\": \"user\", \"content\": prompt}],\n"
            "        )\n"
            "        parts = [\n"
            "            getattr(block, \"text\", \"\")\n"
            "            for block in (getattr(message, \"content\", None) or [])\n"
            "        ]\n"
            "        usage = getattr(message, \"usage\", None)\n"
            "        prompt_tokens = int(getattr(usage, \"input_tokens\", 0) or 0)\n"
            "        completion_tokens = int(getattr(usage, \"output_tokens\", 0) or 0)\n"
            "        return (\n"
            "            \"\".join(parts).strip(),\n"
            "            TokenUsage(\n"
            "                provider=\"anthropic\",\n"
            "                model=ai_settings.model,\n"
            "                prompt_tokens=prompt_tokens,\n"
            "                completion_tokens=completion_tokens,\n"
            "                total_tokens=prompt_tokens + completion_tokens,\n"
            "                estimated_cost=0.0,\n"
            "            ),\n"
            "        )\n"
            "\n"
            "    def _query_mistral(\n"
            "        self,\n"
            "        *,\n"
            "        prompt: str,\n"
            "        temperature: float,\n"
            "        ai_settings: AISettings,\n"
            "        client: Any,\n"
            "    ) -> tuple[str, TokenUsage]:\n"
            "        \"\"\"Mistral's SDK uses `chat.complete`, not `chat.completions`.\"\"\"\n"
            "        completion = client.chat.complete(\n"
            "            model=ai_settings.model,\n"
            "            temperature=temperature,\n"
            "            top_p=ai_settings.top_p,\n"
            "            max_tokens=ai_settings.max_output_tokens,\n"
            "            messages=[{\"role\": \"user\", \"content\": prompt}],\n"
            "        )\n"
            "        usage = getattr(completion, \"usage\", None)\n"
            "        prompt_tokens = int(getattr(usage, \"prompt_tokens\", 0) or 0)\n"
            "        completion_tokens = int(getattr(usage, \"completion_tokens\", 0) or 0)\n"
            "        return (\n"
            "            str(completion.choices[0].message.content).strip(),\n"
            "            TokenUsage(\n"
            "                provider=\"mistral\",\n"
            "                model=ai_settings.model,\n"
            "                prompt_tokens=prompt_tokens,\n"
            "                completion_tokens=completion_tokens,\n"
            "                total_tokens=int(\n"
            "                    getattr(usage, \"total_tokens\", prompt_tokens + "
            "completion_tokens)\n"
            "                    or 0\n"
            "                ),\n"
            "                estimated_cost=0.0,\n"
            "            ),\n"
            "        )\n"
            "\n"
            "    def _query_gemini(\n"
            "        self,\n"
            "        *,\n"
            "        prompt: str,\n"
            "        temperature: float,\n"
            "        ai_settings: AISettings,\n"
            "        client: Any | None = None,\n"
            "    ) -> tuple[str, Any]:\n"
        ),
        rationale="Add completion paths for the four providers ARCH-23 makes routable.",
    ),
    Edit(
        edit_id="23-1.6",
        applied_marker="gemini = client if client is not None else self.gemini_client",
        anchor=(
            "        logger.info(\"Sending request to Gemini.\")\n"
            "        from google.generativeai.types import GenerationConfig\n"
            "\n"
            "        model = self.gemini_model.GenerativeModel(ai_settings.model)\n"
            "        response = model.generate_content(\n"
            "            prompt,\n"
            "            generation_config=GenerationConfig(\n"
            "                temperature=temperature,\n"
            "                max_output_tokens=ai_settings.max_output_tokens,\n"
            "            ),\n"
            "        )\n"
            "        return str(response.text).strip(), response\n"
        ),
        replacement=(
            "        logger.info(\"Sending request to Gemini.\")\n"
            "        from google.genai import types as genai_types\n"
            "\n"
            "        # ARCH-23: the tenant client when the factory supplied one, the\n"
            "        # platform client otherwise. The tenant client is never assigned\n"
            "        # to `self._gemini_client` — caching it would make one tenant's\n"
            "        # key the default for every later request on this worker, which\n"
            "        # is the process-global defect in a slower disguise.\n"
            "        gemini = client if client is not None else self.gemini_client\n"
            "        response = gemini.models.generate_content(\n"
            "            model=ai_settings.model,\n"
            "            contents=prompt,\n"
            "            config=genai_types.GenerateContentConfig(\n"
            "                temperature=temperature,\n"
            "                max_output_tokens=ai_settings.max_output_tokens,\n"
            "                top_p=ai_settings.top_p,\n"
            "            ),\n"
            "        )\n"
            "        return str(response.text).strip(), response\n"
        ),
        rationale="Gemini completions on the client-object SDK, tenant-aware.",
    ),
    Edit(
        edit_id="23-1.7",
        applied_marker="_COMPLETION_DISPATCH",
        anchor=(
            "        configured = self._validate_provider(ai_settings=ai_settings)\n"
            "\n"
            "        def call(provider: str) -> tuple[str, TokenUsage]:\n"
            "            if provider == \"groq\":\n"
            "                return self._query_groq(\n"
            "                    prompt=prompt,\n"
            "                    temperature=temperature,\n"
            "                    ai_settings=ai_settings,\n"
            "                    client=byok_client if provider == configured else None,\n"
            "                )\n"
            "            text, raw = self._query_gemini(\n"
            "                prompt=prompt, temperature=temperature, ai_settings=ai_settings\n"
            "            )\n"
            "            usage = raw.usage_metadata\n"
            "            return text, TokenUsage(\n"
            "                provider=\"gemini\",\n"
            "                model=ai_settings.model,\n"
            "                prompt_tokens=usage.prompt_token_count,\n"
            "                completion_tokens=usage.candidates_token_count,\n"
            "                total_tokens=usage.total_token_count,\n"
            "                estimated_cost=0.0,\n"
            "            )\n"
        ),
        replacement=(
            "        configured = self._validate_provider(ai_settings=ai_settings)\n"
            "\n"
            "        def call(provider: str) -> tuple[str, TokenUsage]:\n"
            "            # The tenant client serves ONLY the provider it was built\n"
            "            # for. If llm_resilience fails over, the failover target is\n"
            "            # the platform's account and `byok_client` must not travel\n"
            "            # with it — `llm_metering._byok_applies` detects the\n"
            "            # divergence at settle time and re-attributes rather than\n"
            "            # stamping ZERO_BYOK on real supplier spend.\n"
            "            client = byok_client if provider == configured else None\n"
            "\n"
            "            if provider in _COMPLETION_DISPATCH:\n"
            "                resolved = client or self._platform_client_for(provider)\n"
            "                return _COMPLETION_DISPATCH[provider](\n"
            "                    self,\n"
            "                    prompt=prompt,\n"
            "                    temperature=temperature,\n"
            "                    ai_settings=ai_settings,\n"
            "                    client=resolved,\n"
            "                )\n"
            "\n"
            "            if provider == \"gemini\":\n"
            "                text, raw = self._query_gemini(\n"
            "                    prompt=prompt,\n"
            "                    temperature=temperature,\n"
            "                    ai_settings=ai_settings,\n"
            "                    client=client,\n"
            "                )\n"
            "                usage = raw.usage_metadata\n"
            "                return text, TokenUsage(\n"
            "                    provider=\"gemini\",\n"
            "                    model=ai_settings.model,\n"
            "                    prompt_tokens=int(\n"
            "                        getattr(usage, \"prompt_token_count\", 0) or 0\n"
            "                    ),\n"
            "                    completion_tokens=int(\n"
            "                        getattr(usage, \"candidates_token_count\", 0) or 0\n"
            "                    ),\n"
            "                    total_tokens=int(\n"
            "                        getattr(usage, \"total_token_count\", 0) or 0\n"
            "                    ),\n"
            "                    estimated_cost=0.0,\n"
            "                )\n"
            "\n"
            "            raise ValueError(\n"
            "                f\"No completion path for provider '{provider}'. This is \"\n"
            "                \"an internal inconsistency: _validate_provider accepted \"\n"
            "                \"it, so the registry and the dispatch table disagree. \"\n"
            "                \"Gate 23-G3 asserts they cannot.\"\n"
            "            )\n"
        ),
        rationale="Dispatch completions across all six providers instead of two.",
    ),
    Edit(
        edit_id="23-1.8",
        applied_marker="def _platform_client_for(self, provider: str) -> Any:",
        anchor="    def _execute_query(\n",
        replacement=(
            "    def _platform_client_for(self, provider: str) -> Any:\n"
            "        \"\"\"The platform's own client for a provider.\n"
            "\n"
            "        Groq and Gemini keep their cached properties, because FlowPilot\n"
            "        holds a key for each and there is exactly one of each. The other\n"
            "        four have `platform_setting=None` in the registry — FlowPilot\n"
            "        holds no key at all — so reaching this branch for them means a\n"
            "        BYOK call lost its tenant client somewhere upstream, and the\n"
            "        honest answer is to say so rather than to raise an\n"
            "        AttributeError three frames deeper.\n"
            "        \"\"\"\n"
            "        key = (provider or \"\").strip().lower()\n"
            "        if key == \"groq\":\n"
            "            return self.groq_client\n"
            "        if key == \"gemini\":\n"
            "            return self.gemini_client\n"
            "        raise ValueError(\n"
            "            f\"FlowPilot holds no platform key for '{key}', so this call \"\n"
            "            \"cannot be served without a tenant credential. Either the \"\n"
            "            \"routing rule requested a platform key for a provider that \"\n"
            "            \"has none, or the tenant credential failed to resolve.\"\n"
            "        )\n"
            "\n"
            "    def _execute_query(\n"
        ),
        rationale="Name the platform-key providers explicitly instead of assuming two.",
    ),
    Edit(
        edit_id="23-1.9",
        applied_marker="_COMPLETION_DISPATCH: dict[str, Any] = {",
        anchor="llm_service = LLMService()\n",
        replacement=(
            "#: ARCH-23. Provider -> completion method, for the five providers that\n"
            "#: take a client and return (text, TokenUsage) directly. Gemini is\n"
            "#: absent because it returns a raw response whose usage lives on\n"
            "#: `usage_metadata` with different field names; folding it in would\n"
            "#: mean a wrapper that exists only to hide one shape difference.\n"
            "#:\n"
            "#: Keyed lower-case to match `_validate_provider`'s return value and\n"
            "#: `llm_resilience`'s provider strings.\n"
            "_COMPLETION_DISPATCH: dict[str, Any] = {\n"
            "    \"groq\": lambda self, **kw: LLMService._query_openai_compatible(\n"
            "        self, provider_label=\"groq\", **kw\n"
            "    ),\n"
            "    \"openai\": lambda self, **kw: LLMService._query_openai_compatible(\n"
            "        self, provider_label=\"openai\", **kw\n"
            "    ),\n"
            "    \"azure_openai\": lambda self, **kw: LLMService._query_openai_compatible(\n"
            "        self, provider_label=\"azure_openai\", **kw\n"
            "    ),\n"
            "    \"mistral\": lambda self, **kw: LLMService._query_mistral(self, **kw),\n"
            "    \"anthropic\": lambda self, **kw: LLMService._query_anthropic(self, **kw),\n"
            "}\n"
            "\n"
            "\n"
            "llm_service = LLMService()\n"
        ),
        rationale="A dispatch table beats a six-branch if/elif nobody reads.",
    ),
    Edit(
        edit_id="23-1.10",
        applied_marker="def supported_task_types(self) -> tuple[str, ...]:",
        anchor=(
            "    def health_check(self) -> bool:\n"
            "        try:\n"
            "            return True\n"
            "        except Exception:\n"
            "            return False\n"
        ),
        replacement=(
            "    def supported_task_types(self) -> tuple[str, ...]:\n"
            "        \"\"\"Every task type the execution layer can actually serve.\n"
            "\n"
            "        ARCH-23. ARCH-22 declared five task types in the BYOK vocabulary\n"
            "        and wired three: a tenant could save a routing policy for\n"
            "        VERIFICATION or EMBEDDING and it did nothing — silently, with no\n"
            "        error, which is the worst shape for a policy control.\n"
            "\n"
            "        Read from the vocabulary rather than listed here, so the two\n"
            "        cannot drift. Gate 23-G14 asserts every entry has at least one\n"
            "        eligible provider.\n"
            "        \"\"\"\n"
            "        return tuple(BYOK_TASK_TYPE_VALUES)\n"
            "\n"
            "    def assert_task_routable(self, *, provider: str, task_type: str) -> None:\n"
            "        \"\"\"Refuse a provider/task pairing the provider cannot serve.\n"
            "\n"
            "        Groq and Anthropic expose no embeddings API. Without this check\n"
            "        an EMBEDDING route naming either would fail inside a document\n"
            "        pipeline hours after the rule was saved, far from the setting\n"
            "        that caused it.\n"
            "        \"\"\"\n"
            "        if not supports_task(provider, task_type):\n"
            "            raise ValueError(\n"
            "                f\"{normalize_provider(provider)} does not serve \"\n"
            "                f\"{task_type} requests.\"\n"
            "            )\n"
            "\n"
            "    def health_check(self) -> bool:\n"
            "        try:\n"
            "            return True\n"
            "        except Exception:\n"
            "            return False\n"
        ),
        rationale="Expose the full task vocabulary and refuse impossible pairings.",
    ),
]


# =====================================================================
# ai_settings_service.py
# =====================================================================

AI_SETTINGS_EDITS: list[Edit] = [
    Edit(
        edit_id="23-2.1",
        applied_marker="from google import genai",
        anchor=(
            "                import google.generativeai as genai\n"
            "\n"
            "                genai.configure(api_key=settings.GEMINI_API_KEY.get_secret_value())\n"
            "                g_model = genai.GenerativeModel(model)\n"
            "                g_model.generate_content(\"ping\")\n"
        ),
        replacement=(
            "                # ARCH-23. Was `genai.configure(...)`, which wrote the\n"
            "                # API key into module-global state — a connection test\n"
            "                # run by one admin would then have set the key for every\n"
            "                # concurrent request on this worker. The client object\n"
            "                # binds the key to an instance and leaves no residue.\n"
            "                from google import genai\n"
            "\n"
            "                probe_client = genai.Client(\n"
            "                    api_key=settings.GEMINI_API_KEY.get_secret_value()\n"
            "                )\n"
            "                probe_client.models.generate_content(\n"
            "                    model=model, contents=\"ping\"\n"
            "                )\n"
        ),
        rationale="The last genai.configure call site in the codebase.",
    ),
]


TARGETS: list[tuple[str, list[Edit]]] = [
    ("backend/app/services/llm_service.py", LLM_SERVICE_EDITS),
    ("backend/app/services/ai_settings_service.py", AI_SETTINGS_EDITS),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-23 anchored edits.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    total_applied = 0
    total_skipped = 0
    failures: list[str] = []

    for rel_path, edits in TARGETS:
        absolute = REPO_ROOT / rel_path
        if not absolute.exists():
            failures.append(f"{rel_path}: file not found")
            continue

        source = absolute.read_text(encoding="utf-8-sig")
        staged = source
        outcomes: list[tuple[str, str]] = []

        try:
            for edit in edits:
                staged, outcome = edit.apply(staged, path=rel_path)
                outcomes.append((edit.edit_id, outcome))
        except AnchorMiss as exc:
            failures.append(str(exc))
            print(f"\n{rel_path}\n  FAILED — file left untouched\n  {exc}")
            continue

        applied = sum(1 for _, o in outcomes if o == "APPLIED")
        skipped = sum(1 for _, o in outcomes if o == "SKIP")
        total_applied += applied
        total_skipped += skipped

        print(f"\n{rel_path}")
        for edit_id, outcome in outcomes:
            print(f"  {edit_id:<10} {outcome}")

        if args.apply and staged != source:
            absolute.write_text(staged, encoding="utf-8", newline="\n")
            print(f"  -> written ({applied} applied, {skipped} already present)")
        elif staged == source:
            print("  -> no change needed (idempotent)")

    print("\n" + "=" * 70)
    print(
        f"{total_applied} edit(s) pending/applied, "
        f"{total_skipped} already present, {len(failures)} failure(s)"
    )
    print("=" * 70)

    if failures:
        return 2
    if args.check and total_applied:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())