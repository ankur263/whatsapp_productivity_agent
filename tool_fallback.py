"""Recovers from Llama-style tool-call leakage on Groq.

Some Groq models occasionally emit `<function=NAME>JSON</function>` directly
inside `msg.content` instead of populating the OpenAI `tool_calls` field.
When that happens, the agent loop would otherwise treat the tag as the user
reply. These helpers detect the pattern, execute the requested tools, and
feed observations back so the model can produce a real natural-language reply.
"""
from __future__ import annotations

import json
import re
from typing import Callable

# Matches: <function=NAME ...optional body... </function>
# The body may appear before OR after a `>` close (Llama variants do both).
_LEAK_PATTERN = re.compile(r"<function=(\w+)([^<]*?)</function>", re.DOTALL)


def parse_leaked_tool_calls(content: str | None) -> list[dict]:
    """Extract leaked tool calls from message text. Returns [{'name', 'args', 'raw'}]."""
    if not content or "<function=" not in content:
        return []
    out: list[dict] = []
    for m in _LEAK_PATTERN.finditer(content):
        name = m.group(1)
        body = m.group(2).strip()
        if body.startswith(">"):
            body = body[1:].strip()
        args: dict = {}
        if body:
            i, j = body.find("{"), body.rfind("}")
            if 0 <= i < j:
                try:
                    args = json.loads(body[i : j + 1])
                except Exception:
                    args = {}
        out.append({"name": name, "args": args, "raw": m.group(0)})
    return out


def absorb_leaked_calls(messages: list, content: str, local_tools: dict[str, Callable]) -> bool:
    """If `content` contains leaked tool calls, execute them and append the
    observations to `messages` so the next model turn can summarize.

    Returns True if at least one leak was handled (caller should continue the
    loop), False otherwise (caller should treat `content` as the final reply).
    """
    leaks = parse_leaked_tool_calls(content)
    if not leaks:
        return False

    messages.append({"role": "assistant", "content": content})

    observations: list[str] = []
    for call in leaks:
        name, args = call["name"], call["args"]
        if name not in local_tools:
            observations.append(f"{name}: Error — unknown tool")
            continue
        try:
            obs = local_tools[name](**args) if isinstance(args, dict) else local_tools[name](args)
        except TypeError:
            try:
                obs = local_tools[name](args)
            except Exception as e:
                obs = f"Error: {e}"
        except Exception as e:
            obs = f"Error: {e}"
        observations.append(f"{name}: {obs}")

    messages.append(
        {
            "role": "user",
            "content": (
                "Tool results:\n"
                + "\n".join(observations)
                + "\n\nReply to the original request in plain natural language, "
                "without any <function=...> tags."
            ),
        }
    )
    return True
