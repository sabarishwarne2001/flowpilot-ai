"""
Supported AI models registry for FlowPilot AI.
Dynamically read by the frontend AI Settings dropdowns.
"""

from app.schemas.ai_settings import AIProvider

AI_MODELS = {
    AIProvider.GROQ: [
        # First entry is the primary default. groq/compound and groq/compound-mini
        # were removed: the Groq API answers them with 404 model_not_found.
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
    ],
    AIProvider.GEMINI: [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-3.5-flash",
    ],
}