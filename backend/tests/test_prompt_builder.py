from app.prompts.intents import PromptIntent
from app.prompts.prompt_builder import PromptBuilder


def test_history_builder_empty():
    history = PromptBuilder.build_history([])
    assert history == "No previous conversation."


def test_history_builder_multiple_messages():
    history = PromptBuilder.build_history(
        [
            {
                "role": "user",
                "content": "Hello",
            },
            {
                "role": "assistant",
                "content": "Hi",
            },
        ]
    )

    assert "User: Hello" in history
    assert "Assistant: Hi" in history


def test_every_intent_has_instructions():
    for intent in PromptIntent:
        instructions = PromptBuilder.get_intent_instructions(intent)

        assert instructions is not None
        assert len(instructions.strip()) > 0


def test_prompt_builder_formats_prompt():
    template = """
Question:
{query}

Context:
{context}

History:
{history}

Instructions:
{intent_instructions}
"""

    prompt = PromptBuilder.build_rag_prompt(
        template=template,
        context="Invoice Number: INV-001",
        history="User: Hello",
        query="What is the invoice number?",
        intent_instructions="Answer directly.",
    )

    assert "{query}" not in prompt
    assert "{context}" not in prompt
    assert "INV-001" in prompt
    assert "Answer directly." in prompt