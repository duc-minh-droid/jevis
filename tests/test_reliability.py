"""Does the deterministic tier actually hold up under repetition?

"It worked once" is not a reliability claim. This runs every deterministic
skill many times against real Notepad, in the dictation phrasings Wispr Flow
actually produces, and reports a pass rate per command.

Each iteration cleans up the tab it created, so the run does not bury the
user in windows.
"""
from __future__ import annotations

import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jevis import agent, skills, tools

ITERATIONS = int(os.environ.get("JEVIS_ITERATIONS", "8"))

COMMANDS = [
    "open notepad and write hello world",
    "Open Notepad and write Hello world.",
    "hey jevis, open notepad and type the quarterly numbers",
    "open note pad and write test one two three",
]


def cleanup() -> None:
    """Close the tab the last command created. Never touches other windows."""
    try:
        snap = tools.attach("Notepad", timeout=2.0)
        tools.menu(snap, ["File", "Close tab"])
    except Exception:
        pass


def check_all_deterministic() -> bool:
    ok = True
    for command in COMMANDS:
        if skills.match(command) is None:
            print(f"  NOT DETERMINISTIC: {command!r}")
            ok = False
    return ok


def main() -> int:
    print(f"deterministic tier, {ITERATIONS} iterations x {len(COMMANDS)} commands\n")
    if not check_all_deterministic():
        print("FAIL: a command fell through to the model planner")
        return 1

    passes: dict[str, int] = defaultdict(int)
    failures: list[str] = []
    durations: list[float] = []
    started = time.time()

    for iteration in range(1, ITERATIONS + 1):
        for command in COMMANDS:
            result = agent.run(command, verbose=False)
            durations.append(result.elapsed)
            if result.ok:
                passes[command] += 1
            else:
                failures.append(f"iter {iteration} {command!r}: {result.error}")
            cleanup()
        print(f"  iteration {iteration}/{ITERATIONS} done")

    total = ITERATIONS * len(COMMANDS)
    scored = sum(passes.values())
    print(f"\n{'command':<56} pass rate")
    for command in COMMANDS:
        rate = passes[command] / ITERATIONS * 100
        print(f"{command[:54]:<56} {passes[command]}/{ITERATIONS}  {rate:5.1f}%")

    mean = sum(durations) / len(durations)
    worst = max(durations)
    print(f"\ntotal {scored}/{total} ({scored / total * 100:.1f}%)")
    print(f"mean {mean:.2f}s, slowest {worst:.2f}s, wall {time.time() - started:.0f}s")

    if failures:
        print("\nfailures:")
        for line in failures[:20]:
            print(f"  {line}")

    return 0 if scored == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
