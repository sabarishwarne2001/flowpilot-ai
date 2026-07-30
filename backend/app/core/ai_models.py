from app.schemas.ai_settings import AIProvider

AI_MODELS = {
    AIProvider.GROQ: [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "deepseek-r1-distill-llama-70b",
    ],
    AIProvider.GEMINI: [
        "gemini-3.5-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ],
    # AIProvider.OPENAI: [
    #     "gpt-5",
    #     "gpt-5-mini",
    # ],
    # AIProvider.CLAUDE: [
    #     "claude-sonnet-4",
    #     "claude-opus-4",
    # ],
}