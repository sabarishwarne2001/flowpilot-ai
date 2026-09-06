"""
Unified LLM Gateway and Prompt Orchestration Service for FlowPilot AI.
ARCH-11.5 Step 1 & 2: Spend ceilings, token metering, resilience and enrichment execution.
ARCH-12 Step 1 & 3: Streaming prompt preparation, system prompt isolation, and metered prompt execution.
ARCH-14 Step 1 & 6: Platform-owned pricing and Vertex billing label guard.
ARCH-22 / ARCH-23: Multi-provider BYOK routing and client factory integration.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core import byok_providers
from app.core.config import settings
from app.models.ai_settings import AISettings
from app.prompts.intents import PromptIntent
from app.prompts.prompt_builder import PromptBuilder
from app.schemas.assistant import TokenUsage
from app.services import llm_resilience
from app.services.llm_resilience import LLMPermanentError, LLMUnavailable

logger = logging.getLogger("app.services.llm_service")

CLASSIFICATION_PROMPT_TEMPLATE = """
You are an expert document classifier.

Analyze the following document and classify it into exactly ONE category.

Valid categories:

- Invoice
- Resume
- Contract
- Purchase Order
- Receipt
- Other

Return ONLY valid JSON.

Schema:

{{
  "document_classification": "Resume",
  "confidence_score": 0.99
}}

Document:

{text}
"""

ENTITY_EXTRACTION_PROMPT_TEMPLATE = """
You are an expert information extraction engine.

Document Type:

{document_classification}

Extract structured information from the document.

Rules:

Invoice / Receipt / Purchase Order

- vendor_name
- total_amount
- currency
- tax_amount
- line_items
- date

Resume

- candidate_name
- email
- phone_number
- core_skills
- degree_education
- years_of_experience

Contract

- agreement_date
- termination_date
- party_names
- governing_law
- value_amount

Other

- key_metadata_tags
- core_entities_mentioned

Return ONLY valid JSON.

Document:

{text}
"""

SUMMARIZATION_PROMPT_TEMPLATE = """
You are an expert document summarization system.

Produce a concise executive summary.

Requirements:

- Professional tone
- No bullet points
- No markdown
- No explanations

Document:

{text}
"""

RAG_SYNTHESIS_PROMPT_TEMPLATE = """
You are FlowPilot AI, an enterprise document intelligence assistant.

Your responsibility is to answer the user's question using ONLY the supplied document context.

Rules:

- Never invent information.
- Never guess.
- Never use outside knowledge.
- If the answer cannot be found in the supplied documents,
  clearly state that the information is unavailable.
- Maintain a professional and objective tone.
- Be concise unless the question requires detailed reasoning.

When generating your response:

1. For simple factual questions:
   - Respond naturally in one concise answer.

2. For analytical, comparison, summarization, or multi-part questions,
   organize the response using the following sections when appropriate:

   Direct Answer

   Key Findings

   Supporting Evidence

   Confidence
   (High / Medium / Low based on the supplied document context.)

3. Never fabricate confidence.
   Lower confidence whenever:
   - retrieved evidence is weak,
   - documents are incomplete,
   - or the answer requires inference.

4. If multiple retrieved documents disagree,
   explain the conflict instead of choosing one.

5. When referring to information,
   use the supplied document context only.


========================
Task-Specific Instructions
========================

{intent_instructions}

========================
Evidence & Citation Guidance
========================

When referring to retrieved information:

- Clearly distinguish between facts that are explicitly supported by the supplied document context and conclusions drawn from multiple pieces of evidence.
- Avoid phrases such as "I think", "I assume", or "it is likely" unless uncertainty is genuinely caused by incomplete document evidence.
- Do not invent document names, page numbers, or citation identifiers.
- Refer naturally to "the supplied documents", "the retrieved document context", or "the available evidence" when appropriate.
- Never claim evidence exists if it is not present in the supplied context.

========================
Context Usage Guidance
========================

The supplied document context may contain information from one or more documents.

When answering:

- Base every statement only on the supplied context.
- Combine information across multiple documents when appropriate.
- If multiple documents disagree, explicitly mention the disagreement.
- Do not assume missing information exists.
- If evidence is weak or incomplete, state that clearly.
- Prefer the strongest supporting evidence available in the supplied context.

========================
Document Context
========================

{context}

========================
Conversation History
========================

{history}

========================
User Question
========================

{query}

Assistant:
"""

GEMINI_RAG_SYNTHESIS_PROMPT_TEMPLATE = RAG_SYNTHESIS_PROMPT_TEMPLATE


from app.core.byok_providers import (
    BYOK_TASK_TYPE_VALUES,
    PROVIDER_ANTHROPIC,
    PROVIDER_AZURE_OPENAI,
    PROVIDER_GEMINI,
    PROVIDER_GROQ,
    PROVIDER_MISTRAL,
    PROVIDER_OPENAI,
    ROUTABLE_PROVIDERS,
    normalize_provider,
    supports_task,
)


class _RoutedProvider:
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = str(value).strip().upper()


class _RoutedAISettings:
    __slots__ = ("_base", "provider", "model")

    def __init__(self, base: Any, provider: str, model: str) -> None:
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "provider", _RoutedProvider(provider))
        object.__setattr__(self, "model", model)

    def __getattr__(self, item: str) -> Any:
        return getattr(object.__getattribute__(self, "_base"), item)

    @classmethod
    def wrap(cls, *, base: Any, decision: Any) -> Any:
        provider = str(decision.provider or "").strip().upper()
        model = str(decision.model_name or "").strip()
        base_provider = str(
            getattr(getattr(base, "provider", None), "value", "")
        ).strip().upper()
        base_model = str(getattr(base, "model", "") or "").strip()

        if not provider or not model:
            return base
        if provider == base_provider and model == base_model:
            return base
        return cls(base, provider, model)


class LLMService:
    """
    Provider-agnostic gateway for all Large Language Model operations.
    """

    def __init__(self) -> None:
        self._groq_client: Any | None = None
        self._gemini_client: Any | None = None

    @property
    def groq_client(self) -> Any:
        if self._groq_client is None:
            if settings.GROQ_API_KEY is None:
                raise ValueError("GROQ_API_KEY is not configured.")
            from groq import Groq

            logger.info("Initializing Groq client.")
            self._groq_client = Groq(api_key=settings.GROQ_API_KEY.get_secret_value())
        return self._groq_client

    @property
    def gemini_client(self) -> Any:
        if settings.GEMINI_API_KEY is None:
            raise ValueError("GEMINI_API_KEY is not configured.")
        if self._gemini_client is None:
            from google import genai

            logger.info("Initializing platform Gemini client.")
            self._gemini_client = genai.Client(
                api_key=settings.GEMINI_API_KEY.get_secret_value()
            )
        return self._gemini_client

    def _validate_provider(self, *, ai_settings: AISettings) -> str:
        raw = ai_settings.provider.value if hasattr(ai_settings.provider, "value") else str(ai_settings.provider)
        try:
            provider = normalize_provider(raw)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"Unsupported LLM provider '{raw}'. Known providers: "
                f"{', '.join(sorted(ROUTABLE_PROVIDERS))}."
            ) from exc

        if provider not in ROUTABLE_PROVIDERS:
            raise ValueError(
                f"'{provider}' is a known provider but is not routable, "
                "so no execution adapter can serve this call."
            )
        return provider.lower()

    def resolve_routing(
        self,
        *,
        db: Session | None,
        organization_id: uuid.UUID | None,
        task_type: str,
        ai_settings: AISettings,
    ) -> tuple[AISettings, Any | None, Any | None]:
        if db is None or organization_id is None:
            return ai_settings, None, None

        from app.services.byok import model_routing_service
        from app.services.byok.provider_clients import (
            ProviderClientFactory,
            ProviderUnavailableError,
        )

        try:
            decision = model_routing_service.resolve(
                db,
                organization_id=organization_id,
                task_type=task_type,
                ai_settings=ai_settings,
            )
        except model_routing_service.RoutingError as exc:
            logger.warning(
                "byok.routing_unresolved",
                extra={"task_type": task_type, "error": str(exc)},
            )
            return ai_settings, None, None

        if decision.origin != "route_rule":
            return ai_settings, None, None

        effective = _RoutedAISettings.wrap(base=ai_settings, decision=decision)

        try:
            self._validate_provider(ai_settings=effective)
        except ValueError:
            logger.warning(
                "byok.route_provider_unsupported_by_executor",
                extra={
                    "task_type": task_type,
                    "route_provider": decision.provider,
                },
            )
            return ai_settings, None, None

        if not decision.use_tenant_key:
            return effective, None, None

        try:
            client, credential_use = ProviderClientFactory.build(
                db,
                organization_id=organization_id,
                provider=decision.provider,
                prefer_tenant_key=True,
            )
        except ProviderUnavailableError as exc:
            logger.warning(
                "byok.client_unavailable_using_platform",
                extra={
                    "task_type": task_type,
                    "provider": decision.provider,
                    "error": str(exc),
                },
            )
            return effective, None, None

        return effective, client, credential_use

    def _query_groq(
        self,
        *,
        prompt: str,
        temperature: float,
        ai_settings: AISettings,
        client: Any | None = None,
    ) -> tuple[str, TokenUsage]:
        logger.info("Sending request to Groq.")
        groq = client if client is not None else self.groq_client
        completion = groq.chat.completions.create(
            model=ai_settings.model,
            temperature=temperature,
            top_p=ai_settings.top_p,
            frequency_penalty=ai_settings.frequency_penalty,
            presence_penalty=ai_settings.presence_penalty,
            max_tokens=ai_settings.max_output_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

        return (
            str(completion.choices[0].message.content).strip(),
            TokenUsage(
                provider="groq",
                model=ai_settings.model,
                prompt_tokens=completion.usage.prompt_tokens,
                completion_tokens=completion.usage.completion_tokens,
                total_tokens=completion.usage.total_tokens,
                estimated_cost=0.0,
            ),
        )

    def _query_openai_compatible(
        self,
        *,
        prompt: str,
        temperature: float,
        ai_settings: AISettings,
        client: Any,
        provider_label: str,
    ) -> tuple[str, TokenUsage]:
        completion = client.chat.completions.create(
            model=ai_settings.model,
            temperature=temperature,
            top_p=ai_settings.top_p,
            frequency_penalty=ai_settings.frequency_penalty,
            presence_penalty=ai_settings.presence_penalty,
            max_tokens=ai_settings.max_output_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = completion.usage
        return (
            str(completion.choices[0].message.content).strip(),
            TokenUsage(
                provider=provider_label,
                model=ai_settings.model,
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
                estimated_cost=0.0,
            ),
        )

    def _query_anthropic(
        self,
        *,
        prompt: str,
        temperature: float,
        ai_settings: AISettings,
        client: Any,
    ) -> tuple[str, TokenUsage]:
        message = client.messages.create(
            model=ai_settings.model,
            max_tokens=ai_settings.max_output_tokens,
            temperature=temperature,
            top_p=ai_settings.top_p,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [
            getattr(block, "text", "")
            for block in (getattr(message, "content", None) or [])
        ]
        usage = getattr(message, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return (
            "".join(parts).strip(),
            TokenUsage(
                provider="anthropic",
                model=ai_settings.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                estimated_cost=0.0,
            ),
        )

    def _query_mistral(
        self,
        *,
        prompt: str,
        temperature: float,
        ai_settings: AISettings,
        client: Any,
    ) -> tuple[str, TokenUsage]:
        completion = client.chat.complete(
            model=ai_settings.model,
            temperature=temperature,
            top_p=ai_settings.top_p,
            max_tokens=ai_settings.max_output_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = getattr(completion, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return (
            str(completion.choices[0].message.content).strip(),
            TokenUsage(
                provider="mistral",
                model=ai_settings.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=int(
                    getattr(usage, "total_tokens", prompt_tokens + completion_tokens)
                    or 0
                ),
                estimated_cost=0.0,
            ),
        )

    def _query_gemini(
        self,
        *,
        prompt: str,
        temperature: float,
        ai_settings: AISettings,
        client: Any | None = None,
    ) -> tuple[str, Any]:
        if settings.GEMINI_BILLING_LABELS_ENABLED and not settings.GEMINI_USE_VERTEX:
            raise ValueError(
                "GEMINI_BILLING_LABELS_ENABLED requires the Vertex backend. "
                "google-generativeai cannot send labels, and google-genai refuses "
                "them against the Gemini Developer API. Reconciliation for Gemini "
                "is ALLOCATED until the Vertex migration lands; see ARCH-14 §14.6."
            )

        logger.info("Sending request to Gemini.")
        from google.genai import types as genai_types

        gemini = client if client is not None else self.gemini_client
        response = gemini.models.generate_content(
            model=ai_settings.model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=ai_settings.max_output_tokens,
                top_p=ai_settings.top_p,
            ),
        )
        return str(response.text).strip(), response

    def _platform_client_for(self, provider: str) -> Any:
        key = (provider or "").strip().lower()
        if key == "groq":
            return self.groq_client
        if key == "gemini":
            return self.gemini_client
        raise ValueError(
            f"FlowPilot holds no platform key for '{key}', so this call "
            "cannot be served without a tenant credential. Either the "
            "routing rule requested a platform key for a provider that "
            "has none, or the tenant credential failed to resolve."
        )

    def _execute_query(
        self,
        *,
        prompt: str,
        temperature: float,
        ai_settings: AISettings,
        byok_client: Any | None = None,
    ) -> tuple[str, TokenUsage]:
        configured = self._validate_provider(ai_settings=ai_settings)

        def call(provider: str) -> tuple[str, TokenUsage]:
            client = byok_client if provider == configured else None

            if provider in _COMPLETION_DISPATCH:
                resolved = client or self._platform_client_for(provider)
                return _COMPLETION_DISPATCH[provider](
                    self,
                    prompt=prompt,
                    temperature=temperature,
                    ai_settings=ai_settings,
                    client=resolved,
                )

            if provider == "gemini":
                text, raw = self._query_gemini(
                    prompt=prompt,
                    temperature=temperature,
                    ai_settings=ai_settings,
                    client=client,
                )
                usage = raw.usage_metadata
                return text, TokenUsage(
                    provider="gemini",
                    model=ai_settings.model,
                    prompt_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                    completion_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
                    total_tokens=int(getattr(usage, "total_token_count", 0) or 0),
                    estimated_cost=0.0,
                )

            raise ValueError(f"No completion path for provider '{provider}'.")

        try:
            outcome = llm_resilience.execute(
                call,
                provider=configured,
                fallback_provider=settings.LLM_FALLBACK_PROVIDER,
            )
        except LLMPermanentError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The AI request could not be processed as sent.",
            ) from exc
        except LLMUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The AI service is temporarily unavailable. Please try again.",
            ) from exc

        return outcome.value

    def _enrichment_call(
        self,
        *,
        operation: str,
        prompt: str,
        temperature: float,
        ai_settings: AISettings,
        db: Session | None = None,
        organization_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        work_item_id: uuid.UUID | None = None,
    ) -> tuple[str, TokenUsage | None]:
        from app.core.request_context import stage
        from app.services import llm_metering

        metered = (
            db is not None
            and organization_id is not None
            and work_item_id is not None
        )

        task_type = (
            byok_providers.TASK_SUMMARY
            if operation == "summary"
            else byok_providers.TASK_EXTRACTION
        )
        effective_settings, byok_client, credential_use = self.resolve_routing(
            db=db if metered else None,
            organization_id=organization_id if metered else None,
            task_type=task_type,
            ai_settings=ai_settings,
        )

        reservation = None
        if metered:
            reservation = llm_metering.reserve_for_enrichment(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                work_item_id=work_item_id,
                operation=operation,
                prompt=prompt,
                ai_settings=effective_settings,
            )

        with stage(
            "llm", provider=getattr(effective_settings.provider, "value", str(effective_settings.provider)), operation=operation
        ):
            response, token_usage = self._execute_query(
                prompt=prompt,
                temperature=temperature,
                ai_settings=effective_settings,
                byok_client=byok_client,
            )

        if reservation is not None and db is not None:
            if credential_use is not None:
                reservation.attach_credential_use(credential_use)
            llm_metering.settle(db, reservation=reservation, token_usage=token_usage)

        return response, token_usage

    def _extract_json(self, raw_text: str) -> dict[str, Any]:
        """Robustly extract and parse JSON from any LLM response."""
        if not raw_text or not str(raw_text).strip():
            return {}

        import re
        cleaned = str(raw_text).strip()

        # 1. Regex search for ```json ... ``` or ``` ... ```
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 2. Try direct json.loads with simple markdown strip
        no_fence = re.sub(r"^```(?:json)?\s*", "", cleaned)
        no_fence = re.sub(r"\s*```$", "", no_fence).strip()
        try:
            return json.loads(no_fence)
        except json.JSONDecodeError:
            pass

        # 3. Outermost { and } substring
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = cleaned[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    cleaned_commas = re.sub(r",\s*([\}\]])", r"\1", candidate)
                    return json.loads(cleaned_commas)
                except json.JSONDecodeError:
                    pass

        logger.warning("llm.json_parse_fallback", extra={"raw_preview": cleaned[:160]})
        return {}

    def _truncate_document(self, text: str) -> str:
        return text[: settings.RAG_MAX_CONTEXT_LENGTH]

    def _detect_prompt_intent(self, query: str) -> PromptIntent:
        normalized = query.lower()
        if any(word in normalized for word in ("summarize", "summary", "overview", "brief")):
            return PromptIntent.SUMMARIZATION
        if any(word in normalized for word in ("compare", "difference", "different", "versus", "vs")):
            return PromptIntent.COMPARISON
        if any(word in normalized for word in ("extract", "list", "show all", "identify")):
            return PromptIntent.EXTRACTION
        if any(word in normalized for word in ("explain", "why", "how")):
            return PromptIntent.EXPLANATION
        if any(word in normalized for word in ("policy", "compliance", "regulation", "legal")):
            return PromptIntent.COMPLIANCE
        return PromptIntent.QUESTION_ANSWERING

    def _build_classification_prompt(self, text: str) -> str:
        return CLASSIFICATION_PROMPT_TEMPLATE.format(text=self._truncate_document(text))

    def _build_entity_prompt(self, *, text: str, document_classification: str) -> str:
        return ENTITY_EXTRACTION_PROMPT_TEMPLATE.format(
            document_classification=document_classification,
            text=self._truncate_document(text),
        )

    def _build_summary_prompt(self, text: str) -> str:
        return SUMMARIZATION_PROMPT_TEMPLATE.format(text=self._truncate_document(text))

    def enrichment_prompts(
        self, *, text: str, document_classification: str = "Other"
    ) -> dict[str, str]:
        return {
            "classify": self._build_classification_prompt(text),
            "entities": self._build_entity_prompt(
                text=text, document_classification=document_classification
            ),
            "summary": self._build_summary_prompt(text),
        }

    def _build_rag_prompt(
        self,
        *,
        query: str,
        context: str,
        history: list[dict[str, str]],
        ai_settings: AISettings,
    ) -> str:
        history_text = PromptBuilder.build_history(history)
        intent = self._detect_prompt_intent(query)
        intent_instructions = PromptBuilder.get_intent_instructions(intent)
        template = (
            RAG_SYNTHESIS_PROMPT_TEMPLATE
            if self._validate_provider(ai_settings=ai_settings) == "groq"
            else GEMINI_RAG_SYNTHESIS_PROMPT_TEMPLATE
        )
        return PromptBuilder.build_rag_prompt(
            template=template,
            context=context[: settings.RAG_MAX_CONTEXT_LENGTH],
            history=history_text,
            query=query,
            intent_instructions=intent_instructions,
        )

    def classify_document(
        self,
        text: str,
        *,
        ai_settings: AISettings,
        db: Session | None = None,
        organization_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        work_item_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        response, _ = self._enrichment_call(
            operation="classify",
            prompt=self._build_classification_prompt(text),
            temperature=settings.LLM_CLASSIFICATION_TEMPERATURE,
            ai_settings=ai_settings,
            db=db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            work_item_id=work_item_id,
        )
        result = self._extract_json(response)
        if not isinstance(result, dict) or "document_classification" not in result:
            return {"document_classification": "Other", "confidence_score": 0.5}
        return result

    def extract_entities(
        self,
        text: str,
        document_classification: str,
        *,
        ai_settings: AISettings,
        db: Session | None = None,
        organization_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        work_item_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        response, _ = self._enrichment_call(
            operation="entities",
            prompt=self._build_entity_prompt(
                text=text, document_classification=document_classification
            ),
            temperature=settings.LLM_ENTITY_EXTRACTION_TEMPERATURE,
            ai_settings=ai_settings,
            db=db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            work_item_id=work_item_id,
        )
        result = self._extract_json(response)
        return result if isinstance(result, dict) else {}

    def generate_summary(
        self,
        text: str,
        *,
        ai_settings: AISettings,
        db: Session | None = None,
        organization_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        work_item_id: uuid.UUID | None = None,
    ) -> str:
        response, _ = self._enrichment_call(
            operation="summary",
            prompt=self._build_summary_prompt(text),
            temperature=settings.LLM_SUMMARIZATION_TEMPERATURE,
            ai_settings=ai_settings,
            db=db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            work_item_id=work_item_id,
        )
        return response.strip()

    def synthesize_response(
        self,
        *,
        db: Session | None = None,
        organization_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
        message_id: uuid.UUID | None = None,
        query: str,
        context: str,
        history: list[dict[str, str]],
        ai_settings: AISettings,
    ) -> tuple[str, TokenUsage]:
        from app.core.request_context import stage
        from app.services import llm_metering

        prompt = self._build_rag_prompt(
            query=query, context=context, history=history, ai_settings=ai_settings
        )

        metered = (
            db is not None
            and organization_id is not None
            and conversation_id is not None
            and message_id is not None
        )

        effective_settings, byok_client, credential_use = self.resolve_routing(
            db=db if metered else None,
            organization_id=organization_id if metered else None,
            task_type=byok_providers.TASK_ASSISTANT,
            ai_settings=ai_settings,
        )

        reservation = None
        if metered:
            reservation = llm_metering.reserve(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                message_id=message_id,
                prompt=prompt,
                ai_settings=effective_settings,
            )

        with stage("llm", provider=getattr(effective_settings.provider, "value", str(effective_settings.provider))):
            response, token_usage = self._execute_query(
                prompt=prompt,
                temperature=effective_settings.temperature,
                ai_settings=effective_settings,
                byok_client=byok_client,
            )

        if db is not None and reservation is not None:
            if credential_use is not None:
                reservation.attach_credential_use(credential_use)
            llm_metering.settle(db, reservation=reservation, token_usage=token_usage)

        return response.strip(), token_usage

    def execute_prompt(
        self, *, prompt: str, temperature: float, ai_settings: AISettings
    ) -> tuple[str, TokenUsage]:
        return self._execute_query(
            prompt=prompt, temperature=temperature, ai_settings=ai_settings
        )

    def system_prompt_for(self, *, query: str, ai_settings: AISettings) -> str:
        intent = self._detect_prompt_intent(query)
        template = (
            RAG_SYNTHESIS_PROMPT_TEMPLATE
            if self._validate_provider(ai_settings=ai_settings) == "groq"
            else GEMINI_RAG_SYNTHESIS_PROMPT_TEMPLATE
        )
        return template.format(
            intent_instructions=PromptBuilder.get_intent_instructions(intent),
            context="",
            history="",
            query="",
        )

    def build_streaming_prompt(
        self,
        *,
        query: str,
        fenced: Any,
        history: list[dict[str, str]],
        digest: str,
        ai_settings: AISettings,
    ) -> str:
        intent = self._detect_prompt_intent(query)
        template = (
            RAG_SYNTHESIS_PROMPT_TEMPLATE
            if self._validate_provider(ai_settings=ai_settings) == "groq"
            else GEMINI_RAG_SYNTHESIS_PROMPT_TEMPLATE
        )

        history_text = PromptBuilder.build_history(history)
        if digest:
            history_text = (
                "Summary of earlier turns:\n"
                f"{digest}\n\n"
                "Recent turns:\n"
                f"{history_text}"
            )

        return PromptBuilder.build_rag_prompt(
            template=template,
            context=fenced.render_for_prompt(),
            history=history_text,
            query=query,
            intent_instructions=PromptBuilder.get_intent_instructions(intent),
        )

    def supported_task_types(self) -> tuple[str, ...]:
        return tuple(BYOK_TASK_TYPE_VALUES)

    def assert_task_routable(self, *, provider: str, task_type: str) -> None:
        if not supports_task(provider, task_type):
            raise ValueError(
                f"{normalize_provider(provider)} does not serve "
                f"{task_type} requests."
            )

    def health_check(self) -> bool:
        try:
            return True
        except Exception:
            return False


_COMPLETION_DISPATCH: dict[str, Any] = {
    "groq": lambda self, **kw: LLMService._query_openai_compatible(
        self, provider_label="groq", **kw
    ),
    "openai": lambda self, **kw: LLMService._query_openai_compatible(
        self, provider_label="openai", **kw
    ),
    "azure_openai": lambda self, **kw: LLMService._query_openai_compatible(
        self, provider_label="azure_openai", **kw
    ),
    "mistral": lambda self, **kw: LLMService._query_mistral(self, **kw),
    "anthropic": lambda self, **kw: LLMService._query_anthropic(self, **kw),
}


llm_service = LLMService()

__all__ = ["LLMService", "llm_service"]