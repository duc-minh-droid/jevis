"""Deterministic handlers for the commands people actually say.

No model runs on this path. A phrase either matches a pattern and produces a
fixed tool sequence with a checkable postcondition, or it does not match and
falls through to the LLM planner. That is the difference between "usually
works" and "works".

Every step carries an `expect` clause. The executor verifies it against the
real UI and retries, so a step that silently no-ops is caught rather than
reported as success.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from . import apps

_FILLER = re.compile(
    r"^(?:hey |ok |okay |please |can you |could you |i want you to |jevis[,: ]*)+",
    re.IGNORECASE,
)


def normalise(text: str) -> str:
    """Strip dictation artefacts without touching the payload text."""
    cleaned = text.strip()
    cleaned = _FILLER.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


@dataclass
class Skill:
    name: str
    pattern: re.Pattern
    build: Callable[[re.Match], list[dict]]


def _app(raw: str) -> str:
    key = apps.resolve(raw)
    if key is None or not apps.get(key).available:
        raise KeyError(raw)
    return key


# "write hello world" is literal. "write a poem about rain" is a request to
# compose something. The deterministic path must not type the request itself,
# so generative phrasing falls through to the planner.
_GENERATIVE = re.compile(
    r"^(?:an?|the|some|me)\s+"
    r"(?:poem|haiku|song|story|joke|essay|summary|email|letter|message|note"
    r"|reply|paragraph|list|recipe|outline|draft|plan|report|apology|excuse)\b"
    r"|^(?:something|anything)\b"
    r"|\babout\s+(?:the\s+)?\w+",
    re.IGNORECASE,
)


def is_generative(text: str) -> bool:
    return bool(_GENERATIVE.search(text.strip()))


def _payload(raw: str) -> str:
    """The literal text to type. Only strip a single trailing sentence stop,
    never internal punctuation, and never leading/trailing quotes the user
    actually dictated as content."""
    text = raw.strip()
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    return text.strip()


# Built from the alias table so a new app needs no regex edit. Longest first,
# so "my default browser" wins over "browser".
APP = "(" + "|".join(
    re.escape(alias).replace(r"\ ", " ") for alias in sorted(apps.ALIASES, key=len, reverse=True)
) + ")"


def _open_and_write(match: re.Match) -> list[dict]:
    app = _app(match.group(1))
    text = _payload(match.group(2))
    if is_generative(text):
        raise KeyError("generative")
    if not apps.get(app).typable:
        # A terminal runs what it receives; a calculator has no text field.
        raise KeyError(f"{app} is not typable")
    return [
        {"tool": "launch", "app": app, "expect": {"app": app}},
        {
            "tool": "type_text",
            "text": text,
            # launch opened a blank document; if it is not blank, something
            # else owns this window and we must not overwrite it.
            "require": {"editor_empty": True},
            "expect": {"editor_contains": text},
        },
    ]


def _open(match: re.Match) -> list[dict]:
    app = _app(match.group(1))
    return [{"tool": "launch", "app": app, "expect": {"app": app}}]


def _write(match: re.Match) -> list[dict]:
    text = _payload(match.group(1))
    if is_generative(text):
        raise KeyError("generative")
    return [{"tool": "type_text", "text": text, "expect": {"editor_contains": text}}]


def _save(_: re.Match) -> list[dict]:
    # An unsaved Notepad tab shows a leading asterisk. Its absence is proof.
    return [{"tool": "menu", "path": ["File", "Save"], "expect": {"title_clean": True}}]


def _close_tab(_: re.Match) -> list[dict]:
    return [{"tool": "menu", "path": ["File", "Close tab"], "expect": {}}]


def _new_tab(_: re.Match) -> list[dict]:
    return [{"tool": "menu", "path": ["File", "New tab"], "expect": {"editor_empty": True}}]


SKILLS = [
    Skill(
        "open_and_write",
        re.compile(rf"^open {APP}(?: and | then |,? )(?:write|type|put|say) (.+)$", re.IGNORECASE),
        _open_and_write,
    ),
    Skill("open", re.compile(rf"^(?:open|launch|start) {APP}\.?$", re.IGNORECASE), _open),
    Skill("write", re.compile(r"^(?:write|type|put) (.+)$", re.IGNORECASE), _write),
    Skill("save", re.compile(r"^save(?: (?:it|this|the file|that))?\.?$", re.IGNORECASE), _save),
    Skill("close_tab", re.compile(r"^close(?: the)? tab\.?$", re.IGNORECASE), _close_tab),
    Skill("new_tab", re.compile(r"^(?:new|open a new) tab\.?$", re.IGNORECASE), _new_tab),
]


# Shown in the command bar so the surface advertises what it can do. Ordered
# by how often they get used, not alphabetically.
EXAMPLES = [
    "open notepad and write hello world",
    "open my default browser",
    "open terminal",
    "open file explorer",
    "new tab",
    "save",
]


def suggest(text: str, limit: int = 3) -> list[str]:
    """Examples worth showing for what has been typed so far."""
    typed = normalise(text).lower()
    if not typed:
        return EXAMPLES[:limit]
    hits = [e for e in EXAMPLES if typed in e.lower()]
    if not hits:
        words = [w for w in typed.split() if len(w) > 2]
        hits = [e for e in EXAMPLES if any(w in e.lower() for w in words)]
    return [e for e in hits if e.lower() != typed][:limit]


def match(instruction: str) -> tuple[str, list[dict]] | None:
    """Return (skill_name, steps) when a deterministic handler covers this."""
    text = normalise(instruction)
    for skill in SKILLS:
        found = skill.pattern.match(text)
        if not found:
            continue
        try:
            return skill.name, skill.build(found)
        except KeyError:
            continue  # unknown app or generative text: let the planner try
    return None
