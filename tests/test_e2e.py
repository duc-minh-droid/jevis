"""End-to-end proof: drive real Notepad through the tool surface, no LLM.

If this passes, actuation works and the agent loop has solid ground to stand
on. The test always targets a fresh temp file so it never touches whatever
the user already has open in Notepad's other tabs.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jevis import tools

TEXT = "hello world"


def main() -> int:
    handle, path = tempfile.mkstemp(suffix=".txt", prefix="jevis_e2e_")
    os.close(handle)
    print(f"target file: {path}")

    try:
        snap = tools.launch("notepad", [path])
        print(f"launched: {snap.window_title!r} ({len(snap.elements)} elements)")

        editor = snap.first_editable()
        print(f"editor: {editor.render()}")

        # Our temp file is empty, so the focused editor must be empty too. If
        # it is not, focus landed on one of the user's other tabs and writing
        # would destroy their unsaved work.
        existing = (editor.value or "").strip()
        assert not existing, f"refusing to write: focused editor already holds {existing[:40]!r}"

        snap = tools.type_text(snap, editor.id, TEXT)

        # Read the value back out of the live control, not out of our own
        # variable, so the assertion reflects the real UI state.
        editor = snap.first_editable()
        in_ui = (editor.value or "").rstrip("\r\n")
        assert in_ui == TEXT, f"UI shows {in_ui!r}, expected {TEXT!r}"
        print(f"verified in UI: {in_ui!r}")

        # Save through the File menu, not ctrl+s: menu invocation is
        # window-scoped, so it works even if the user has focused something else.
        tools.menu(snap, ["File", "Save"])
        time.sleep(1.2)
        with open(path, "r", encoding="utf-8") as f:
            on_disk = f.read().rstrip("\r\n")
        assert on_disk == TEXT, f"disk has {on_disk!r}, expected {TEXT!r}"
        print(f"verified on disk: {on_disk!r}")

        try:
            tools.menu(snap, ["File", "Close tab"])  # close only the tab this test opened
        except Exception as exc:
            # Cleanup runs after every assertion has already passed.
            print(f"note: tab cleanup raised {type(exc).__name__}: {exc}")
        print("PASS")
        return 0
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        shot = tools.screenshot()
        print(f"screenshot: {shot}")
        return 1
    finally:
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    raise SystemExit(main())
