from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)

from agent import Agent  # noqa: E402  — imported after load_dotenv so env is populated


def run_cli(default_user: str) -> None:
    agent = Agent()
    user_id = default_user
    print("WhatsApp Productivity Agent CLI")
    print("Commands: /user <id>, /quit")
    while True:
        q = input(f"[{user_id}] > ").strip()
        if not q:
            continue
        if q.lower() in {"/q", "/quit", "quit", "exit"}:
            break
        if q.startswith("/user "):
            user_id = q.split(" ", 1)[1].strip() or user_id
            print(f"Switched to user: {user_id}")
            continue
        ans = agent.run(user_id=user_id, question=q)
        print(ans)


def run_smoke() -> int:
    os.environ["LLM_BACKEND"] = "mock"
    agent = Agent()
    user = f"smoke_{int(time.time())}"
    prompts = [
        "What is 12 * 7?",
        "What time is it?",
        "Create a task: buy milk",
        "Show my tasks",
    ]
    print("Running smoke tests...")
    for p in prompts:
        out = agent.run(user_id=user, question=p)
        print(f"Q: {p}\nA: {out}\n")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="cli_user")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run_smoke() if args.smoke else run_cli(args.user))
