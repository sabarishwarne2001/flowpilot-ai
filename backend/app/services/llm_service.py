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

from app.core.config import settings

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
You are FlowPilot AI.

Answer the user's question using ONLY the supplied document context.

Rules:

- Never invent information.
- Never guess.
- If the answer is unavailable in the supplied documents,
  clearly state that the information could not be found.
- Prefer concise answers.
- Maintain professional tone.

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

        return str(
            completion
            .choices[0]
            .message
            .content
        ).strip()

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
    ) -> str:
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

        return self._query_gemini(
            prompt=prompt,
            temperature=temperature,
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
    ) -> str:
        """
        Execute an LLM request with simple retry support for transient
        provider failures.
        """

        last_exception: Exception | None = None

        for attempt in range(retries + 1):

            try:

                return self._query_provider(
                    prompt=prompt,
                    temperature=temperature,
                )

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

        raise last_exception  # type: ignore[misc]

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

        history_lines = [
            f"{message['role'].capitalize()}: {message['content']}"
            for message in history
        ]

        history_text = (
            "\n".join(history_lines)
            if history_lines
            else "No previous conversation."
        )

        return RAG_SYNTHESIS_PROMPT_TEMPLATE.format(
            context=context[
                : settings.RAG_MAX_CONTEXT_LENGTH
            ],
            history=history_text,
            query=query,
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

        response = self._retry_query(
            prompt=prompt,
            temperature=0.0,
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

        response = self._retry_query(
            prompt=prompt,
            temperature=0.1,
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

        response = self._retry_query(
            prompt=prompt,
            temperature=0.3,
        )

        return response.strip()

    def synthesize_response(
        self,
        *,
        query: str,
        context: str,
        history: list[dict[str, str]],
    ) -> str:
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

        response = self._retry_query(
            prompt=prompt,
            temperature=settings.LLM_TEMPERATURE,
        )

        return response.strip()
    

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
    


