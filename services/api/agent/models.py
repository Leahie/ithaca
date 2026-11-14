"""Model factory. LLM_PROVIDER=anthropic|openrouter; per-role override with AGENT_MODEL_<ROLE>.

TODO (B5): OpenRouter free-model whitelist with rate-limit fallback.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from services.api.integrations.config import IntegrationNotConfigured, env

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"


def get_model(role: str = "main") -> BaseChatModel:
    provider = env("LLM_PROVIDER", "anthropic").lower()
    model_id = env(f"AGENT_MODEL_{role.upper()}") or env("AGENT_MODEL")

    if provider == "anthropic":
        if not env("ANTHROPIC_API_KEY"):
            raise IntegrationNotConfigured("Anthropic", "ANTHROPIC_API_KEY")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model_id or DEFAULT_ANTHROPIC_MODEL, max_tokens=4096)

    if provider == "openrouter":
        if not env("OPENROUTER_API_KEY"):
            raise IntegrationNotConfigured("OpenRouter", "OPENROUTER_API_KEY")
        if not model_id:
            raise IntegrationNotConfigured("OpenRouter", "AGENT_MODEL (a model id that supports tool calling)")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_id,
            base_url="https://openrouter.ai/api/v1",
            api_key=env("OPENROUTER_API_KEY"),
            use_responses_api=False,
        )

    raise ValueError(f"LLM_PROVIDER must be 'anthropic' or 'openrouter', got {provider!r}")
