"""
Unified LLM Gateway and Prompt Orchestration Service for FlowPilot AI.
ARCH-11.5 Step 1 & 2: Spend ceilings, token metering, resilience and enrichment execution.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai_settings import AISettings
from app.prompts import RAG_PROMPT_VERSION
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


class LLMService:
    """
    Provider-agnostic gateway for all Large Language Model operations.
    """

    def __init__(self) -> None:
        self._groq_client: Any | None = None
        self._gemini_model: Any | None = None

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
    def gemini_model(self) -> Any:
        if settings.GEMINI_API_KEY is None:
            raise ValueError("GEMINI_API_KEY is not configured.")
        import google.generativeai as genai

        logger.info("Initializing Gemini client.")
        genai.configure(api_key=settings.GEMINI_API_KEY.get_secret_value())
        return genai

    def _validate_provider(self, *, ai_settings: AISettings) -> str:
        provider = ai_settings.provider.value.strip().lower()
        supported = {"groq", "gemini"}
        if provider not in supported:
            raise ValueError(
                f"Unsupported LLM provider '{provider}'. Supported providers: {sorted(supported)}."
            )
        return provider

    def _query_groq(
        self,
        *,
        prompt: str,
        temperature: float,
        ai_settings: AISettings,
    ) -> tuple[str, TokenUsage]:
        logger.info("Sending request to Groq.")
        completion = self.groq_client.chat.completions.create(
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
                estimated_cost=(
                    (completion.usage.prompt_tokens / 1000) * ai_settings.input_cost_per_1k_tokens
                    + (completion.usage.completion_tokens / 1000) * ai_settings.output_cost_per_1k_tokens
                ),
            ),
        )

    def _query_gemini(
        self,
        *,
        prompt: str,
        temperature: float,
        ai_settings: AISettings,
    ) -> tuple[str, Any]:
        logger.info("Sending request to Gemini.")
        from google.generativeai.types import GenerationConfig

        model = self.gemini_model.GenerativeModel(ai_settings.model)
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=temperature,
                max_output_tokens=ai_settings.max_output_tokens,
            ),
        )
        return str(response.text).strip(), response

    def _execute_query(
        self,
        *,
        prompt: str,
        temperature: float,
        ai_settings: AISettings,
    ) -> tuple[str, TokenUsage]:
        """Run the provider call under classification, backoff and a breaker."""
        configured = self._validate_provider(ai_settings=ai_settings)

        def call(provider: str) -> tuple[str, TokenUsage]:
            if provider == "groq":
                return self._query_groq(
                    prompt=prompt, temperature=temperature, ai_settings=ai_settings
                )
            text, raw = self._query_gemini(
                prompt=prompt, temperature=temperature, ai_settings=ai_settings
            )
            usage = raw.usage_metadata
            return text, TokenUsage(
                provider="gemini",
                model=ai_settings.model,
                prompt_tokens=usage.prompt_token_count,
                completion_tokens=usage.candidates_token_count,
                total_tokens=usage.total_token_count,
                estimated_cost=(
                    (usage.prompt_token_count / 1000) * ai_settings.input_cost_per_1k_tokens
                    + (usage.candidates_token_count / 1000) * ai_settings.output_cost_per_1k_tokens
                ),
            )

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
        """Reserve, call, settle for enrichment tasks."""
        from app.core.request_context import stage
        from app.services import llm_metering

        metered = (
            db is not None
            and organization_id is not None
            and work_item_id is not None
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
                ai_settings=ai_settings,
            )

        with stage("llm", provider=ai_settings.provider.value, operation=operation):
            response, token_usage = self._execute_query(
                prompt=prompt, temperature=temperature, ai_settings=ai_settings
            )

        if reservation is not None and db is not None:
            llm_metering.settle(db, reservation=reservation, token_usage=token_usage)

        return response, token_usage

    def _extract_json(self, raw_text: str) -> dict[str, Any]:
        cleaned = raw_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    pass
            logger.error("Unable to parse JSON response from LLM.")
            raise ValueError("Model returned invalid JSON.")

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
        """Exposed so callers can price enrichment before execution."""
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
        return self._extract_json(response)

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
        return self._extract_json(response)

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

        reservation = None
        if db is not None and organization_id is not None and conversation_id is not None and message_id is not None:
            reservation = llm_metering.reserve(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                message_id=message_id,
                prompt=prompt,
                ai_settings=ai_settings,
            )

        with stage("llm", provider=ai_settings.provider.value):
            response, token_usage = self._execute_query(
                prompt=prompt,
                temperature=ai_settings.temperature,
                ai_settings=ai_settings,
            )

        if db is not None and reservation is not None:
            llm_metering.settle(db, reservation=reservation, token_usage=token_usage)

        return response.strip(), token_usage

    def health_check(self) -> bool:
        try:
            return True
        except Exception:
            return False


llm_service = LLMService()

__all__ = ["LLMService", "llm_service"]