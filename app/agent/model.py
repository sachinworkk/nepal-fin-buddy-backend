from strands.models.openai import OpenAIModel

from app.config import get_settings


def build_model():
    """The chat model, via OpenRouter's OpenAI-compatible API. Tests replace this."""
    s = get_settings()
    return OpenAIModel(
        client_args={"api_key": s.openrouter_api_key, "base_url": s.openrouter_base_url},
        model_id=s.chat_model,
    )
