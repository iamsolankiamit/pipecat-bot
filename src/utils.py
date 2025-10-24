"""Utility functions for World of Doors bot."""

import os
from loguru import logger

from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.anthropic.llm import AnthropicLLMService


def create_llm():
    """Create LLM service based on environment variable.

    Supports:
    - anthropic (default)
    - openai

    Set LLM_PROVIDER environment variable to choose.
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment")

        model = os.getenv("OPENAI_MODEL", "gpt-4")
        logger.info(f"Using OpenAI LLM: {model}")

        return OpenAILLMService(
            api_key=api_key,
            model=model
        )

    elif provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment")

        model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        logger.info(f"Using Anthropic LLM: {model}")

        return AnthropicLLMService(
            api_key=api_key,
            model=model
        )

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}. Use 'openai' or 'anthropic'")
