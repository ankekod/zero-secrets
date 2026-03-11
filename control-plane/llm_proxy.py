"""
LLM proxy — reconstructs full conversation context and forwards requests to the Anthropic API.
"""

import os
import logging
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

client = AsyncAnthropic()

SYSTEM_PROMPT = """You are a helpful AI agent running inside a secure sandbox.
You can help with tasks by reasoning through them step by step.

When the user asks you to create files, respond with a JSON block like this:
```json
{"action": "create_file", "path": "filename.txt", "content": "file contents here"}
```

When you are done with the task, include:
```json
{"action": "done", "summary": "Brief summary of what you accomplished"}
```

You can create multiple files in a single response. Always explain what you're doing.
"""


async def invoke_llm(
    history: list[dict],
    new_messages: list[dict],
    model: str | None = None,
) -> dict:
    """
    Call the Anthropic Messages API with full conversation history + new messages.
    Returns the assistant's response.
    """
    model = model or MODEL

    # Anthropic requires system content as a top-level parameter, not in the messages array.
    # Collect system-role messages from history and merge them into the system prompt.
    all_messages = list(history) + list(new_messages)

    system_parts = [SYSTEM_PROMPT]
    chat_messages = []
    for msg in all_messages:
        if msg.get("role") == "system":
            system_parts.append(msg["content"])
        else:
            chat_messages.append(msg)

    system = "\n\n".join(system_parts)

    logger.info(
        f"LLM proxy: sending {len(chat_messages)} messages to {model} "
        f"({len(list(history))} history + {len(new_messages)} new)"
    )

    response = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=chat_messages,
    )

    assistant_message = {
        "role": "assistant",
        "content": response.content[0].text,
    }

    tokens_used = response.usage.input_tokens + response.usage.output_tokens

    return {
        "message": assistant_message,
        "tokens_used": tokens_used,
        "model": model,
    }


async def check_llm_health() -> dict:
    """Check if the Anthropic API key is configured."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return {"status": "healthy", "provider": "anthropic", "model": MODEL}
    return {"status": "unhealthy", "error": "ANTHROPIC_API_KEY not set"}
