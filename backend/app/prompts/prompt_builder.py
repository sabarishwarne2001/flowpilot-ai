from __future__ import annotations

from app.prompts.intents import PromptIntent


class PromptBuilder:
    """
    Centralized builder for all conversational prompts.

    Responsibilities:
    - Build conversation history
    - Build task-specific instructions
    - Assemble the final prompt

    This class contains no provider-specific logic.
    """

    @staticmethod
    def build_history(
        history: list[dict[str, str]],
    ) -> str:
        if not history:
            return "No previous conversation."

        return "\n".join(
            f"{message['role'].capitalize()}: {message['content']}"
            for message in history
        )

    @staticmethod
    def get_intent_instructions(
        intent: PromptIntent,
    ) -> str:
        return {
            PromptIntent.QUESTION_ANSWERING: """
Answer the user's question directly and accurately using only the supplied document context.
Do not include unnecessary explanation.
""",
            PromptIntent.SUMMARIZATION: """
Produce a concise executive summary highlighting the most important information.
Focus on the overall meaning rather than isolated facts.
""",
            PromptIntent.COMPARISON: """
Compare the relevant information clearly.
Highlight similarities, differences, and important observations.
Do not omit conflicting information.
""",
            PromptIntent.EXTRACTION: """
Extract the requested information exactly as it appears in the supplied documents.
Present the extracted information clearly.
Do not infer missing values.
""",
            PromptIntent.EXPLANATION: """
Explain the requested concept using only the supplied document context.
Provide enough detail for understanding while remaining concise.
""",
            PromptIntent.COMPLIANCE: """
Evaluate the supplied document only against the information contained within it.
Do not claim legal compliance unless it is explicitly supported by the document.
Clearly state any uncertainty.
""",
        }[intent]
    
    @staticmethod
    def build_rag_prompt(
        *,
        template: str,
        context: str,
        history: str,
        query: str,
        intent_instructions: str,
    ) -> str:
        """
        Assemble the final conversational RAG prompt.
        """

        return template.format(
            context=context,
            history=history,
            query=query,
            intent_instructions=intent_instructions,
        )