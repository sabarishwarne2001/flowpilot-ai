"""
Supported AI models registry for FlowPilot AI.
Dynamically read by the frontend AI Settings dropdowns.
"""

from app.schemas.ai_settings import AIProvider

AI_MODELS = {
    AIProvider.GROQ: [
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "qwen/qwen3.6-27b",
        "qwen/qwen3.8-27b",
        "groq/compound-mini",
    ],
    AIProvider.GEMINI: [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-3.5-flash",
    ],
}