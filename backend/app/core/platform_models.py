"""The models Servabo serves on its OWN provider keys (N-054), by provider (lower case).

Kept apart from app/core/ai_models.py, which re-exports it, because it must import nothing:
app/core/config.py checks GROQ_MODEL_NAME against it while the settings are being built, and
ai_models imports the schemas package, whose modules import the settings (a cycle at start-up).
"""

PLATFORM_SERVED_MODELS: dict[str, tuple[str, ...]] = {
    "groq": ("openai/gpt-oss-20b", "openai/gpt-oss-120b"),
}
