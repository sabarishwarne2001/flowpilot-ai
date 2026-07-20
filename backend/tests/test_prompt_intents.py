from app.prompts.intents import PromptIntent


def test_prompt_intent_values():
    assert PromptIntent.QUESTION_ANSWERING.value == "question_answering"
    assert PromptIntent.SUMMARIZATION.value == "summarization"
    assert PromptIntent.COMPARISON.value == "comparison"
    assert PromptIntent.EXTRACTION.value == "extraction"
    assert PromptIntent.EXPLANATION.value == "explanation"
    assert PromptIntent.COMPLIANCE.value == "compliance"