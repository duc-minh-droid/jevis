"""Two models, split by what each is good at.

Jev (TypeSafe /v1/systemone) is a classifier: it returns a typed choice with a
probability distribution, not prose. That makes it the router and the judge.
It cannot write text, so it never gets asked to.

NVIDIA NIM serves a small instruct model for the two jobs that need language:
turning an instruction into a tool plan, and generating body text.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

JEV_URL = "https://api.typesafe.ai/v1/systemone"
NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


def load_env(path: str | Path = ".env") -> None:
    """Minimal .env loader. Secrets stay in the file, never in source."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        os.environ.setdefault(name.strip(), value.strip())


class BrainError(RuntimeError):
    pass


def _post(url: str, payload: dict, api_key: str, timeout: float = 45.0) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        raise BrainError(f"{url} returned {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise BrainError(f"{url} unreachable: {exc.reason}") from exc


# --- Jev: classification and judging ---------------------------------------


@dataclass
class Choice:
    choice: str
    confidence: float
    probabilities: dict[str, float]


def jev_choice(state: str, instructions: str, options: dict[str, str]) -> Choice:
    """Ask Jev to pick one option. Returns the pick plus its distribution."""
    api_key = os.environ.get("JEV_API_KEY")
    if not api_key:
        raise BrainError("JEV_API_KEY not set")
    payload = {
        "state": state,
        "model": "jev-latest",
        "questions": {
            "q": {"type": "choice", "instructions": instructions, "criteria": options}
        },
    }
    answer = _post(JEV_URL, payload, api_key)["answers"]["q"]
    return Choice(
        choice=answer["choice"],
        confidence=float(answer.get("confidence", 0.0)),
        probabilities=answer.get("probabilities", {}),
    )


ROUTE_OPTIONS = {
    "simple_action": "Open, click, close, or type a short literal phrase the user already gave verbatim.",
    "needs_text": "The user wants written content the agent must compose, such as an email, a poem, or a summary.",
    "needs_plan": "Several dependent steps, conditionals, or navigation the agent must work out first.",
    "reject": "Unsafe, destructive, or outside a desktop automation agent's job.",
}


def route(instruction: str) -> Choice:
    """Classify an instruction so the expensive models only run when needed."""
    return jev_choice(
        state=instruction,
        instructions="A desktop automation agent received this instruction. What does it need?",
        options=ROUTE_OPTIONS,
    )


def judge(instruction: str, window_title: str, editor_value: str) -> Choice:
    """Did the task actually succeed? Judged from observed UI state, not from
    the agent's own claim that it finished."""
    state = (
        f"Instruction: {instruction}\n"
        f"Window title after the agent acted: {window_title}\n"
        f"Text now in the editor: {editor_value!r}"
    )
    return jev_choice(
        state=state,
        instructions="Did the agent complete the instruction correctly?",
        options={
            "success": "The observed state satisfies the instruction.",
            "failure": "The observed state does not satisfy the instruction.",
        },
    )


# --- NIM: planning and text generation -------------------------------------


def nim_chat(system: str, user: str, max_tokens: int = 512, temperature: float = 0.2) -> str:
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise BrainError("NVIDIA_API_KEY not set")
    payload = {
        "model": NIM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        # Nemotron is a reasoning model and otherwise streams its chain of
        # thought into `content`. We want the answer, not the deliberation.
        "chat_template_kwargs": {"thinking": False},
    }
    data = _post(NIM_URL, payload, api_key)
    return data["choices"][0]["message"]["content"].strip()


def _planner_system() -> str:
    """Built at call time so the allowed-app list reflects this machine."""
    from . import apps

    return f"""You convert a desktop instruction into a JSON tool plan.

Available tools, one JSON object per step:
  {{"tool": "launch", "app": "<app name>", "args": ["<optional file path>"]}}
  {{"tool": "type_text", "text": "<text to put in the focused editor>"}}
  {{"tool": "menu", "path": ["File", "Save"]}}

Allowed apps, and nothing else: {", ".join(apps.available_names())}

Rules:
- Reply with a JSON array of steps and nothing else. No prose, no code fence.
- "launch" must come first if the app is not already open.
- If the instruction names an app that is NOT in the allowed list, reply with
  exactly [] . Never substitute a different app. Opening the wrong application
  is worse than doing nothing.
- Only notepad accepts type_text. Never type into a terminal or a browser.
- Do not invent tools or fields."""


def plan(instruction: str, body_text: str | None = None) -> list[dict]:
    """Turn an instruction into a list of tool steps."""
    prompt = instruction
    if body_text is not None:
        prompt += f"\n\nUse exactly this text as the content:\n{body_text}"
    raw = nim_chat(_planner_system(), prompt)
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        raise BrainError(f"planner did not return a JSON array: {raw[:200]!r}")
    try:
        steps = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise BrainError(f"planner returned invalid JSON: {raw[start:end + 1][:200]!r}") from exc
    if not isinstance(steps, list):
        raise BrainError("planner returned a non-list plan")

    from . import apps

    # The planner is told to return [] for an unsupported app. Enforce it here
    # too: substituting a different application is the worst failure mode.
    if not steps:
        raise BrainError(
            "no supported app for that instruction. jevis can open: "
            + ", ".join(apps.available_names())
        )
    for step in steps:
        if step.get("tool") == "launch":
            key = apps.resolve(step.get("app", "")) or step.get("app", "")
            if key not in apps.REGISTRY:
                raise BrainError(f"planner chose {step.get('app')!r}, which is not an allowed app")
            step["app"] = key
        if step.get("tool") == "type_text":
            step.setdefault("require", {"editor_empty": True})
    return steps


def write_text(instruction: str) -> str:
    """Compose the body text the user asked for."""
    return nim_chat(
        "You write the requested text and nothing else. No preamble, no quotes around it.",
        instruction,
        max_tokens=400,
        temperature=0.6,
    )
