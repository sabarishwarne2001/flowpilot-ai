from enum import StrEnum


class PromptIntent(StrEnum):
    """Supported prompt intents for conversational RAG."""

    QUESTION_ANSWERING = "question_answering"
    SUMMARIZATION = "summarization"
    COMPARISON = "comparison"
    EXTRACTION = "extraction"
    EXPLANATION = "explanation"
    COMPLIANCE = "compliance"