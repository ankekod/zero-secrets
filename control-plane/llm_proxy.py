"""
LLM Proxy — the control plane's interface to the actual LLM.

The sandbox sends only new messages. The control plane:
1. Looks up the full conversation history
2. Reconstructs the complete context
3. Forwards to the Anthropic API
4. Returns the response

The sandbox never talks to Anthropic directly — it doesn't even hold the API key.
"""

import os
import logging
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")

# Client is initialized at module load. It reads ANTHROPIC_API_KEY from the environment.
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

    # Anthropic requires system content as a top-level parameter — system-role
    # messages are not allowed in the messages array. Collect any system messages
    # from history (e.g. the task seeded by launch-sandbox.sh) and merge them
    # into the system prompt.
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

    # Anthropic reports input and output tokens separately
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
