"""
Unified LLM Gateway and Prompt Orchestration Service for FlowPilot AI.

Provides a provider-agnostic interface supporting Groq and Gemini for:

- Document Classification
- Entity Extraction
- Document Summarization
- Conversational RAG
- Future Tool Calling
- Future Multi-Agent Workflows

This service owns prompt generation and provider communication only.
Business logic remains in higher-level orchestration services.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.prompts.intents import PromptIntent

from app.prompts.prompt_builder import PromptBuilder

from app.prompts import RAG_PROMPT_VERSION

from app.core.config import settings

from app.schemas.assistant import TokenUsage

from fastapi import HTTPException, status

from groq import RateLimitError

logger = logging.getLogger("app.services.llm_service")


# ============================================================================
# Prompt Templates
# ============================================================================

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


    # ========================================================================
    # Provider Initialization
    # ========================================================================

    @property
    def groq_client(self) -> Any:
        """
        Lazily initialize the Groq client.
        """

        if self._groq_client is None:

            if settings.GROQ_API_KEY is None:
                raise ValueError(
                    "GROQ_API_KEY is not configured."
                )

            from groq import Groq

            logger.info(
                "Initializing Groq client."
            )

            self._groq_client = Groq(
                api_key=settings.GROQ_API_KEY.get_secret_value(),
            )

        return self._groq_client

    @property
    def gemini_model(self) -> Any:
        """
        Lazily initialize the Gemini model.
        """

        if self._gemini_model is None:

            if settings.GEMINI_API_KEY is None:
                raise ValueError(
                    "GEMINI_API_KEY is not configured."
                )

            import google.generativeai as genai

            logger.info(
                "Initializing Gemini client."
            )

            genai.configure(
                api_key=settings.GEMINI_API_KEY.get_secret_value(),
            )

            self._gemini_model = genai.GenerativeModel(
                settings.GEMINI_MODEL_NAME,
            )

        return self._gemini_model

    # ========================================================================
    # Provider Helpers
    # ========================================================================

    def _validate_provider(self) -> str:
        """
        Validate and return the configured provider.
        """

        provider = (
            settings.LLM_PROVIDER
            .strip()
            .lower()
        )

        supported = {
            "groq",
            "gemini",
        }

        if provider not in supported:

            raise ValueError(
                f"Unsupported LLM provider '{provider}'. "
                f"Supported providers: {sorted(supported)}."
            )

        return provider
    
    def _get_rag_temperature(self) -> float:
        """
        Return the provider-specific temperature used for conversational RAG.
        """

        provider = self._validate_provider()

        if provider == "groq":
            return settings.GROQ_RAG_TEMPERATURE

        return settings.GEMINI_RAG_TEMPERATURE
    
    def _get_rag_prompt_template(self) -> str:
        """
        Return the provider-specific conversational RAG prompt template.
        """

        provider = self._validate_provider()

        if provider == "groq":
            return RAG_SYNTHESIS_PROMPT_TEMPLATE

        return GEMINI_RAG_SYNTHESIS_PROMPT_TEMPLATE

    def _query_groq(
        self,
        *,
        prompt: str,
        temperature: float,
    ) -> str:
        """
        Execute a Groq chat completion.
        """

        logger.info(
            "Sending request to Groq."
        )

        completion = (
            self.groq_client.chat.completions.create(
                model=settings.GROQ_MODEL_NAME,
                temperature=temperature,
                max_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )
        )

        return (
            str(
                completion
                .choices[0]
                .message
                .content
            ).strip(),
            TokenUsage(
                provider="groq",
                model=settings.GROQ_MODEL_NAME,
                prompt_tokens=completion.usage.prompt_tokens,
                completion_tokens=completion.usage.completion_tokens,
                total_tokens=completion.usage.total_tokens,
                estimated_cost=(
                    (completion.usage.prompt_tokens / 1000)
                    * settings.TOKEN_COST_PER_1K_INPUT
                    + (completion.usage.completion_tokens / 1000)
                    * settings.TOKEN_COST_PER_1K_OUTPUT
                ),
            )
        )

    def _query_gemini(
        self,
        *,
        prompt: str,
        temperature: float,
    ) -> str:
        """
        Execute a Gemini generation request.
        """

        logger.info(
            "Sending request to Gemini."
        )

        from google.generativeai.types import (
            GenerationConfig,
        )

        response = (
            self.gemini_model.generate_content(
                prompt,
                generation_config=GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
                ),
            )
        )

        return str(response.text).strip()

    def _query_provider(
        self,
        *,
        prompt: str,
        temperature: float,
    ) -> tuple[str, TokenUsage]:
        """
        Route a request to the configured provider.
        """

        provider = self._validate_provider()

        logger.info(
            "Using '%s' provider.",
            provider,
        )

        if provider == "groq":
            return self._query_groq(
                prompt=prompt,
                temperature=temperature,
            )

        response = self._query_gemini(
            prompt=prompt,
            temperature=temperature,
        )

        return (
            response,
            TokenUsage(
                provider="gemini",
                model=settings.GEMINI_MODEL_NAME,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost=0.0,
            ),
        )
    
    # ========================================================================
    # Internal Helpers
    # ========================================================================

    def _retry_query(
        self,
        *,
        prompt: str,
        temperature: float,
        retries: int = 2,
    ) -> tuple[str, TokenUsage]:
        """
        Execute an LLM request with retry support.

        Provider-specific failures are converted into
        application-level exceptions so the API can
        return appropriate HTTP status codes.
        """

        last_exception: Exception | None = None

        for attempt in range(retries + 1):

            try:

                return self._query_provider(
                    prompt=prompt,
                    temperature=temperature,
                )

            #
            # Provider rate limit
            #
            except RateLimitError as exc:

                logger.warning(
                    "LLM rate limit reached."
                )

                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        "The AI service is temporarily busy. "
                        "Please try again in a few minutes."
                    ),
                )

            #
            # Any other provider failure
            #
            except Exception as exc:

                last_exception = exc

                logger.warning(
                    "LLM request failed (attempt %d/%d).",
                    attempt + 1,
                    retries + 1,
                )

        logger.exception(
            "LLM request failed after all retry attempts."
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The AI service is temporarily unavailable. "
                "Please try again later."
            ),
        )

    def _extract_json(
        self,
        raw_text: str,
    ) -> dict[str, Any]:
        """
        Extract the first valid JSON object from an LLM response.

        This is more robust than assuming the model returns only JSON.
        """

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

                    return json.loads(
                        cleaned[start : end + 1]
                    )

                except json.JSONDecodeError:
                    pass

            logger.error(
                "Unable to parse JSON response from LLM."
            )

            raise ValueError(
                "Model returned invalid JSON."
            )

    def _truncate_document(
        self,
        text: str,
    ) -> str:
        """
        Truncate document text before prompt generation.

        Keeps prompt construction consistent across all tasks.
        """

        return text[
            : settings.RAG_MAX_CONTEXT_LENGTH
        ]

    # ========================================================================
    # Prompt Builders
    # ========================================================================


    def _detect_prompt_intent(
        self,
        query: str,
    ) -> PromptIntent:
        """
        Detect the user's intent so the prompt can adapt its instructions.
        """

        normalized = query.lower()

        if any(word in normalized for word in (
            "summarize",
            "summary",
            "overview",
            "brief",
        )):
            return PromptIntent.SUMMARIZATION

        if any(word in normalized for word in (
            "compare",
            "difference",
            "different",
            "versus",
            "vs",
        )):
            return PromptIntent.COMPARISON

        if any(word in normalized for word in (
            "extract",
            "list",
            "show all",
            "identify",
        )):
            return PromptIntent.EXTRACTION

        if any(word in normalized for word in (
            "explain",
            "why",
            "how",
        )):
            return PromptIntent.EXPLANATION

        if any(word in normalized for word in (
            "policy",
            "compliance",
            "regulation",
            "legal",
        )):
            return PromptIntent.COMPLIANCE

        return PromptIntent.QUESTION_ANSWERING

    def _build_classification_prompt(
        self,
        text: str,
    ) -> str:

        return CLASSIFICATION_PROMPT_TEMPLATE.format(
            text=self._truncate_document(text),
        )

    def _build_entity_prompt(
        self,
        *,
        text: str,
        document_classification: str,
    ) -> str:

        return ENTITY_EXTRACTION_PROMPT_TEMPLATE.format(
            document_classification=document_classification,
            text=self._truncate_document(text),
        )

    def _build_summary_prompt(
        self,
        text: str,
    ) -> str:

        return SUMMARIZATION_PROMPT_TEMPLATE.format(
            text=self._truncate_document(text),
        )

    def _build_rag_prompt(
        self,
        *,
        query: str,
        context: str,
        history: list[dict[str, str]],
    ) -> str:
        """
        Construct the conversational RAG prompt.
        """

        history_text = PromptBuilder.build_history(history)

        intent = self._detect_prompt_intent(query)

        logger.debug(
            "Building RAG prompt version %s for intent '%s'.",
            RAG_PROMPT_VERSION,
            intent.value,
        )

        intent_instructions = PromptBuilder.get_intent_instructions(
            intent
        )

        return PromptBuilder.build_rag_prompt(
            template=self._get_rag_prompt_template(),
            context=context[: settings.RAG_MAX_CONTEXT_LENGTH],
            history=history_text,
            query=query,
            intent_instructions=intent_instructions,
        )
    
    # ========================================================================
    # Public API
    # ========================================================================

    def classify_document(
        self,
        text: str,
    ) -> dict[str, Any]:
        """
        Classify a document into one of the supported business document
        categories.

        Returns:
            {
                "document_classification": "...",
                "confidence_score": ...
            }
        """

        logger.info(
            "Running document classification."
        )

        prompt = self._build_classification_prompt(
            text,
        )

        response, _ = self._retry_query(
            prompt=prompt,
            temperature=settings.LLM_CLASSIFICATION_TEMPERATURE,
        )

        return self._extract_json(
            response,
        )

    def extract_entities(
        self,
        text: str,
        document_classification: str,
    ) -> dict[str, Any]:
        """
        Extract structured entities from a document.

        The extraction prompt is automatically adapted to the
        detected document classification.
        """

        logger.info(
            "Running entity extraction for '%s'.",
            document_classification,
        )

        prompt = self._build_entity_prompt(
            text=text,
            document_classification=document_classification,
        )

        response, _ = self._retry_query(
            prompt=prompt,
            temperature=settings.LLM_ENTITY_EXTRACTION_TEMPERATURE,
        )

        return self._extract_json(
            response,
        )

    def generate_summary(
        self,
        text: str,
    ) -> str:
        """
        Generate an executive summary for a document.
        """

        logger.info(
            "Generating document summary."
        )

        prompt = self._build_summary_prompt(
            text,
        )

        response, _ = self._retry_query(
            prompt=prompt,
            temperature=settings.LLM_SUMMARIZATION_TEMPERATURE,
        )

        return response.strip()

    def synthesize_response(
        self,
        *,
        query: str,
        context: str,
        history: list[dict[str, str]],
    ) -> tuple[str, TokenUsage]:
        """
        Generate a conversational RAG response.

        This method powers the AI Assistant introduced in Sprint 5.
        """

        logger.info(
            "Generating conversational response."
        )

        prompt = self._build_rag_prompt(
            query=query,
            context=context,
            history=history,
        )

        response, token_usage = self._retry_query(
            prompt=prompt,
            temperature=self._get_rag_temperature(),
        )

        return response.strip(), token_usage
    

    # ========================================================================
    # Diagnostics
    # ========================================================================

    def health_check(self) -> bool:
        """
        Verify that the configured LLM provider can be initialized.

        This does not send an API request. It simply ensures the
        provider client can be created successfully.
        """

        try:

            provider = self._validate_provider()

            if provider == "groq":
                _ = self.groq_client
            else:
                _ = self.gemini_model

            return True

        except Exception:

            logger.exception(
                "LLM service health check failed."
            )

            return False


llm_service = LLMService()
    


