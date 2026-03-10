"""
Agent — the core loop that runs inside the sandbox.

This is intentionally simple to keep the demo focused on the
infrastructure pattern, not the agent logic. A production agent
would have tool calling, planning, error recovery, etc.

The agent:
1. Gets its task
2. Sends messages to the LLM via the control plane gateway
3. Parses actions (create_file, done) from the LLM response
4. Executes actions locally
5. Syncs files to S3
6. Loops until done
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

    logger.info(f"Agent starting for session {session_id}")
    logger.info(f"Control plane: {control_plane_url}")
    logger.info(f"Workspace: {WORKSPACE_DIR}")

    # ── Security demo: prove env vars are gone ──
    env_check = {
        "SESSION_TOKEN": os.environ.get("SESSION_TOKEN", "NOT SET"),
        "CONTROL_PLANE_URL": os.environ.get("CONTROL_PLANE_URL", "NOT SET"),
        "SESSION_ID": os.environ.get("SESSION_ID", "NOT SET"),
    }
    logger.info(f"Environment variable check: {env_check}")
    logger.info("(All should be 'NOT SET' — values are in memory only)")

    # Initialize the gateway (our only way to talk to the outside world)
    gateway = ControlPlaneGateway(control_plane_url, token)
    file_sync = FileSync(gateway)

    # The agent loop: send task, get response, execute actions, repeat
    iteration = 0
    task_message_sent = False

    while iteration < MAX_ITERATIONS:
        iteration += 1
        logger.info(f"── Iteration {iteration}/{MAX_ITERATIONS} ──")

        # First iteration: the task is already seeded in conversation
        # history by the launch script. We just ask the LLM to proceed.
        if not task_message_sent:
            new_messages = [{"role": "user", "content": "Please complete the task described above. Create any files needed in the workspace."}]
            task_message_sent = True
        else:
            # Subsequent iterations: send action results as context
            new_messages = [{"role": "user", "content": "Please continue with the task. If you're done, include a done action."}]

        # Call LLM through the control plane
        try:
            result = await gateway.invoke_llm(new_messages)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            await asyncio.sleep(2)
            continue

        assistant_content = result["message"]["content"]
        logger.info(f"LLM response ({result.get('tokens_used', '?')} tokens):")
        logger.info(f"{assistant_content[:500]}...")

        # Parse and execute actions
        actions = parse_actions(assistant_content)

        if not actions:
            logger.info("No actions in response. Asking for next steps...")
            continue

        done = False
        action_results = []
        for action in actions:
            result_msg = execute_action(action)
            action_results.append(result_msg)
            if action["action"] == "done":
                done = True

        # Sync any created files to S3
        synced = await file_sync.sync()
        if synced:
            logger.info(f"Files synced to S3: {synced}")

        if done:
            logger.info("=== Agent completed task ===")
            break

        # Persist action results for next iteration context
        await gateway.persist_messages([
            {"role": "user", "content": "Action results:\n" + "\n".join(action_results)}
        ])

    if iteration >= MAX_ITERATIONS:
        logger.warning(f"Agent hit max iterations ({MAX_ITERATIONS})")

    logger.info("Sandbox shutting down.")


def main():
    parser = argparse.ArgumentParser(description="Sandbox Agent")
    parser.add_argument("--token", required=True, help="Session token")
    parser.add_argument("--control-plane", required=True, help="Control plane URL")
    parser.add_argument("--session", required=True, help="Session ID")
    args = parser.parse_args()

    asyncio.run(run_agent(args.token, args.control_plane, args.session))


if __name__ == "__main__":
    main()
