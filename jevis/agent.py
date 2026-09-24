"""The loop: match, act, verify, retry.

Three tiers, in order of how much they can be trusted:

1. A deterministic skill matched the phrase. Fixed tool sequence, checkable
   postcondition, no model involved. This tier is reliable.
2. No skill matched, so Jev routes and a small model plans. Verified the same
   way, retried with the failure fed back, but a planner can always be wrong.
3. Jev rejected the instruction. Nothing runs.

Verification is deterministic in every tier: read the UI back and compare. The
LLM judge only speaks when a step declared no checkable postcondition.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from . import apps, brain, skills, tools

MAX_STEPS = 8
SKILL_ATTEMPTS = 3
PLAN_ATTEMPTS = 2


# on_event(kind, data). The command bar uses it to animate the plan and to
# highlight the element being acted on; --trace writes the same stream as JSON.
#   match   {tier, skill, steps}      a tier was chosen and a plan exists
#   step    {index, tool, label}      a step is about to run
#   target  {index, rect, role, name} the UIA element the step acts on
#   verify  {index, ok, clause, detail}
#   attempt {attempt, error}          a whole attempt failed and will retry
Listener = Callable[[str, dict], None]


def _quiet(_kind: str, _data: dict) -> None:
    pass


def describe(step: dict) -> str:
    """One short human line for a plan step."""
    name = step.get("tool")
    if name == "launch":
        return f"launch {step.get('app')}"
    if name == "type_text":
        text = step.get("text", "")
        return f"type {text[:32]!r}" + ("..." if len(text) > 32 else "")
    if name == "menu":
        return "menu " + " > ".join(step.get("path") or [])
    return str(name)


def _tree(snap: tools.Snapshot, limit: int = 14) -> list[str]:
    """The first lines of what the tool surface saw, for traces."""
    return snap.render().splitlines()[: limit + 1]


def _rect(control) -> list[int] | None:
    try:
        r = control.BoundingRectangle
        if r.right > r.left and r.bottom > r.top:
            return [r.left, r.top, r.right, r.bottom]
    except Exception:
        pass
    return None


class VerifyError(RuntimeError):
    """A step ran but the UI does not show its expected result."""


@dataclass
class Result:
    instruction: str
    tier: str = "unmatched"
    skill: str | None = None
    steps: list[dict] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    attempts: int = 0
    verdict: str = "unknown"
    confidence: float = 0.0
    error: str | None = None
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and self.verdict == "success"


def _norm(text: str) -> str:
    return " ".join(text.replace("\r\n", "\n").split())


def _verify(step: dict, snap: tools.Snapshot | None, clause: str = "expect") -> None:
    """Compare a declared condition against what the UI actually shows."""
    expect = step.get(clause) or {}
    if not expect or snap is None:
        return

    if "app" in expect:
        spec = apps.get(expect["app"])
        if snap.process not in spec.processes:
            raise VerifyError(
                f"expected a {expect['app']} window ({'/'.join(sorted(spec.processes))}), "
                f"got {snap.process or 'unknown'!r} titled {snap.window_title[:40]!r}"
            )

    if "editor_contains" in expect:
        want = _norm(expect["editor_contains"])
        try:
            got = _norm(snap.first_editable().value or "")
        except tools.ToolError as exc:
            raise VerifyError(f"no editable control to verify against: {exc}") from exc
        if want not in got:
            raise VerifyError(f"editor shows {got[:60]!r}, expected it to contain {want[:60]!r}")

    if expect.get("editor_empty"):
        try:
            got = _norm(snap.first_editable().value or "")
        except tools.ToolError as exc:
            raise VerifyError(f"no editable control to verify against: {exc}") from exc
        if got:
            raise VerifyError(f"expected an empty editor, found {got[:60]!r}")

    if expect.get("title_clean"):
        # Notepad marks an unsaved tab with a leading asterisk.
        if snap.window_title.startswith("*"):
            raise VerifyError(f"document still unsaved: title is {snap.window_title!r}")


def _checked(step: dict, snap, clause: str, index: int, emit: Listener) -> None:
    """_verify, reported. Only steps that declare the clause emit anything."""
    if not step.get(clause):
        return
    try:
        _verify(step, snap, clause)
    except VerifyError as exc:
        emit("verify", {"index": index, "ok": False, "clause": clause, "detail": str(exc)})
        raise
    emit("verify", {"index": index, "ok": True, "clause": clause,
                    "detail": ", ".join(sorted(step[clause]))})


def _execute(steps: list[dict], log: list[str], window=None,
             emit: Listener = _quiet) -> tools.Snapshot | None:
    snap = tools.snapshot(window) if window is not None else None
    for index, step in enumerate(steps[:MAX_STEPS], 1):
        name = step.get("tool")
        emit("step", {"index": index, "tool": name, "label": describe(step)})
        if name == "launch":
            snap = tools.launch(step.get("app", ""), step.get("args") or None)
            emit("target", {"index": index, "rect": _rect(snap.window), "role": "Window",
                            "name": snap.window_title, "elements": len(snap.elements),
                            "handle": getattr(snap.window, "NativeWindowHandle", 0),
                            "tree": _tree(snap)})
            log.append(f"{index}. launch {step.get('app')} -> {snap.window_title!r}")
        elif name == "type_text":
            if snap is None:
                raise tools.ToolError("type_text with no target window")
            # Preconditions run before the action, not after, so a refusal
            # prevents the damage instead of reporting it.
            _checked(step, snap, "require", index, emit)
            editor = snap.first_editable()
            emit("target", {"index": index, "rect": list(editor.rect), "role": editor.role,
                            "name": editor.name, "elements": len(snap.elements),
                            "tree": _tree(snap)})
            snap = tools.type_text(snap, editor.id, step.get("text", ""))
            log.append(f"{index}. type_text {step.get('text', '')[:40]!r}")
        elif name == "menu":
            if snap is None:
                raise tools.ToolError("menu with no target window")
            path = step.get("path") or []
            try:
                head = snap.find(path[0], role="MenuItem")
                emit("target", {"index": index, "rect": list(head.rect), "role": head.role,
                                "name": head.name, "elements": len(snap.elements),
                                "tree": _tree(snap)})
            except (tools.ToolError, IndexError):
                pass
            snap = tools.menu(snap, path)
            log.append(f"{index}. menu {path}")
        else:
            raise tools.ToolError(f"unknown tool in plan: {name!r}")
        _checked(step, snap, "expect", index, emit)
    return snap


def _final_verdict(result: Result, steps: list[dict], snap: tools.Snapshot | None) -> None:
    """Deterministic when the plan declared postconditions, LLM-judged only
    when it did not."""
    if any(step.get("expect") for step in steps):
        result.verdict = "success"
        result.confidence = 1.0
        return
    title = snap.window_title if snap else ""
    try:
        value = (snap.first_editable().value or "") if snap else ""
    except tools.ToolError:
        value = ""
    verdict = brain.judge(result.instruction, title, value)
    result.verdict, result.confidence = verdict.choice, verdict.confidence


def run(instruction: str, window=None, verbose: bool = True,
        on_event: Listener | None = None) -> Result:
    started = time.time()
    result = Result(instruction=instruction)
    emit = on_event or _quiet

    # Tier 1: deterministic skill. No network call, no model.
    matched = skills.match(instruction)
    if matched:
        result.tier, result.skill = "skill", matched[0]
        result.steps = matched[1]
        if verbose:
            print(f"skill: {matched[0]} (deterministic)")
        emit("match", {"tier": "skill", "skill": matched[0],
                       "steps": [describe(step) for step in result.steps]})
        last = ""
        for attempt in range(1, SKILL_ATTEMPTS + 1):
            result.attempts = attempt
            result.log.clear()
            try:
                snap = _execute(result.steps, result.log, window, emit)
                _final_verdict(result, result.steps, snap)
                result.elapsed = time.time() - started
                return result
            except (VerifyError, tools.ToolError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                emit("attempt", {"attempt": attempt, "error": last})
                if verbose:
                    print(f"attempt {attempt} failed: {last}")
                time.sleep(0.6 * attempt)
        result.error, result.verdict = last, "failure"
        result.elapsed = time.time() - started
        return result

    # Tier 2 and 3: the model decides.
    brain.load_env()
    decision = brain.route(instruction)
    result.tier = decision.choice
    if verbose:
        print(f"route: {decision.choice} (confidence {decision.confidence:.2f})")

    if decision.choice == "reject":
        result.error = "refused: instruction classified as unsafe or out of scope"
        result.verdict = "failure"
        result.elapsed = time.time() - started
        return result

    feedback = ""
    last = ""
    for attempt in range(1, PLAN_ATTEMPTS + 1):
        result.attempts = attempt
        result.log.clear()
        try:
            body = brain.write_text(instruction) if decision.choice == "needs_text" else None
            prompt = instruction if not feedback else f"{instruction}\n\nThe previous plan failed: {feedback}\nProduce a corrected plan."
            result.steps = brain.plan(prompt, body_text=body)
            if verbose:
                print(f"plan: {result.steps}")
            emit("match", {"tier": decision.choice, "skill": None,
                           "steps": [describe(step) for step in result.steps]})
            snap = _execute(result.steps, result.log, window, emit)
            _final_verdict(result, result.steps, snap)
            result.elapsed = time.time() - started
            return result
        except Exception as exc:
            last = feedback = f"{type(exc).__name__}: {exc}"
            emit("attempt", {"attempt": attempt, "error": last})
            if verbose:
                print(f"attempt {attempt} failed: {last}")

    result.error, result.verdict = last, "failure"
    result.elapsed = time.time() - started
    return result


def trace_record(instruction: str, result: Result, events: list[dict]) -> dict:
    return {
        "instruction": instruction,
        "normalised": skills.normalise(instruction),
        "ok": result.ok,
        "tier": result.tier,
        "skill": result.skill,
        "attempts": result.attempts,
        "elapsed": round(result.elapsed, 3),
        "error": result.error,
        "events": events,
    }


def trace(instruction: str, window=None) -> dict:
    """Run one instruction and return every event it produced, timestamped."""
    started = time.time()
    events: list[dict] = []

    def record(kind: str, data: dict) -> None:
        events.append({"t": round(time.time() - started, 3), "kind": kind, **data})

    result = run(instruction, window=window, verbose=False, on_event=record)
    return trace_record(instruction, result, events)


def main() -> int:
    import sys

    args = sys.argv[1:]
    as_json = "--trace" in args
    args = [a for a in args if a != "--trace"]
    if not args:
        print('usage: python -m jevis.agent [--trace] "open notepad and write hello world"')
        return 2
    if as_json:
        record = trace(" ".join(args))
        print(json.dumps(record, indent=2))
        return 0 if record["ok"] else 1
    result = run(" ".join(args))
    print(f"\n--- {'OK' if result.ok else 'FAILED'} in {result.elapsed:.1f}s "
          f"(tier={result.tier} attempts={result.attempts}) ---")
    for line in result.log:
        print(f"  {line}")
    if result.error:
        print(f"  error: {result.error}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
