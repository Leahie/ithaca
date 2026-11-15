"""Model factory. LLM_PROVIDER=anthropic|openrouter; per-role override with AGENT_MODEL_<ROLE>.

Roles: main (the guide), designer, observer, vision (reading furniture photos). Every one of them
runs on the same fast model unless a role is given its own.

TODO (B5): OpenRouter free-model whitelist with rate-limit fallback.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from services.api.integrations.config import IntegrationNotConfigured, env

# The fast model, for every agent: the guide talks while people walk, so a short wait matters more
# than depth. AGENT_MODEL (or AGENT_MODEL_DESIGNER etc.) picks another.
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"

# How hard each role thinks before it answers (Anthropic's `effort`). The guide and the observer
# answer short questions with a tool call or two, which is what "low" is for: less thinking, fewer
# words, a faster reply. The designer reasons about a room and the photo reader about an image, so
# they keep the model's own default ("medium" on Haiku 5.5). AGENT_EFFORT (every role) or
# AGENT_EFFORT_<ROLE> picks another: low | medium | high | xhigh | max.
DEFAULT_EFFORT: dict[str, str] = {"main": "low", "observer": "low"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def model_id(role: str = "main") -> str:
    """The model a role runs on, as it will be sent."""
    return env(f"AGENT_MODEL_{role.upper()}") or env("AGENT_MODEL") or DEFAULT_ANTHROPIC_MODEL


def effort(role: str = "main") -> str | None:
    """The effort a role runs at, or None for the model's default."""
    chosen = (env(f"AGENT_EFFORT_{role.upper()}") or env("AGENT_EFFORT")).lower() or DEFAULT_EFFORT.get(role, "")
    return chosen if chosen in EFFORTS else None


def get_model(role: str = "main") -> BaseChatModel:
    provider = env("LLM_PROVIDER", "anthropic").lower()

    if provider == "anthropic":
        if not env("ANTHROPIC_API_KEY"):
            raise IntegrationNotConfigured("Anthropic", "ANTHROPIC_API_KEY")
        from langchain_anthropic import ChatAnthropic

        level = effort(role)
        return ChatAnthropic(model=model_id(role), max_tokens=4096, **({"effort": level} if level else {}))

    if provider == "openrouter":
        if not env("OPENROUTER_API_KEY"):
            raise IntegrationNotConfigured("OpenRouter", "OPENROUTER_API_KEY")
        chosen = env(f"AGENT_MODEL_{role.upper()}") or env("AGENT_MODEL")
        if not chosen:
            raise IntegrationNotConfigured("OpenRouter", "AGENT_MODEL (a model id that supports tool calling)")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=chosen,
            base_url="https://openrouter.ai/api/v1",
            api_key=env("OPENROUTER_API_KEY"),
            use_responses_api=False,
        )

    raise ValueError(f"LLM_PROVIDER must be 'anthropic' or 'openrouter', got {provider!r}")
