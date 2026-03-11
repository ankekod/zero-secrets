"""
Agent loop running inside the sandbox.

Sends messages to the LLM via the control plane, parses action blocks
from responses, executes them locally, and syncs output files to S3.
"""

import argparse
import asyncio
import json
import os
import re
import logging
import sys

from gateway import ControlPlaneGateway
from file_sync import FileSync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SANDBOX] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

WORKSPACE_DIR = "/workspace"
MAX_ITERATIONS = 10


def parse_actions(text: str) -> list[dict]:
    """Extract JSON action blocks from the LLM response."""
    actions = []
    # Find ```json ... ``` blocks
    pattern = r"```json\s*(\{.*?\})\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    for match in matches:
        try:
            action = json.loads(match)
            if "action" in action:
                actions.append(action)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse action JSON: {match[:100]}")
    return actions


def execute_action(action: dict) -> str:
    """Execute a parsed action. Returns a status message."""
    if action["action"] == "create_file":
        path = action.get("path", "output.txt")
        content = action.get("content", "")
        full_path = os.path.join(WORKSPACE_DIR, path)

        # Ensure directory exists
        os.makedirs(os.path.dirname(full_path) if os.path.dirname(full_path) else WORKSPACE_DIR, exist_ok=True)

        with open(full_path, "w") as f:
            f.write(content)

        logger.info(f"Created file: {path} ({len(content)} bytes)")
        return f"File '{path}' created successfully ({len(content)} bytes)"

    elif action["action"] == "done":
        summary = action.get("summary", "Task completed")
        logger.info(f"Agent done: {summary}")
        return f"DONE: {summary}"

    else:
        return f"Unknown action: {action['action']}"


async def run_agent(token: str, control_plane_url: str, session_id: str):
    """Main agent loop."""

    logger.info(f"Agent starting (session={session_id}, workspace={WORKSPACE_DIR})")

    # Verify env vars were stripped by entrypoint.sh
    env_check = {
        "SESSION_TOKEN": os.environ.get("SESSION_TOKEN", "NOT SET"),
        "CONTROL_PLANE_URL": os.environ.get("CONTROL_PLANE_URL", "NOT SET"),
        "SESSION_ID": os.environ.get("SESSION_ID", "NOT SET"),
    }
    logger.info(f"Environment check (all should be NOT SET): {env_check}")

    gateway = ControlPlaneGateway(control_plane_url, token)
    file_sync = FileSync(gateway)

    iteration = 0
    task_message_sent = False

    while iteration < MAX_ITERATIONS:
        iteration += 1
        logger.info(f"── Iteration {iteration}/{MAX_ITERATIONS} ──")

        # First iteration: task is already in history, prompt the LLM to begin
        if not task_message_sent:
            new_messages = [{"role": "user", "content": "Please complete the task described above. Create any files needed in the workspace."}]
            task_message_sent = True
        else:
            new_messages = [{"role": "user", "content": "Please continue with the task. If you're done, include a done action."}]


        try:
            result = await gateway.invoke_llm(new_messages)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            await asyncio.sleep(2)
            continue

        assistant_content = result["message"]["content"]
        logger.info(f"LLM response ({result.get('tokens_used', '?')} tokens): {assistant_content[:500]}...")

        actions = parse_actions(assistant_content)

        if not actions:
            logger.info("No actions in response, continuing...")
            continue

        done = False
        action_results = []
        for action in actions:
            result_msg = execute_action(action)
            action_results.append(result_msg)
            if action["action"] == "done":
                done = True

        synced = await file_sync.sync()
        if synced:
            logger.info(f"Synced files: {synced}")

        if done:
            logger.info("Task complete")
            break

        await gateway.persist_messages([
            {"role": "user", "content": "Action results:\n" + "\n".join(action_results)}
        ])

    if iteration >= MAX_ITERATIONS:
        logger.warning(f"Reached max iterations ({MAX_ITERATIONS})")

    logger.info("Shutting down.")


def main():
    parser = argparse.ArgumentParser(description="Sandbox Agent")
    parser.add_argument("--token", required=True, help="Session token")
    parser.add_argument("--control-plane", required=True, help="Control plane URL")
    parser.add_argument("--session", required=True, help="Session ID")
    args = parser.parse_args()

    asyncio.run(run_agent(args.token, args.control_plane, args.session))


if __name__ == "__main__":
    main()
