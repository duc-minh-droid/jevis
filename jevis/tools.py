"""The complete action surface exposed to the agent.

Seven tools, deliberately. Small models pick correctly from seven options and
fail from thirty. Every mutating tool returns a fresh Snapshot, so the agent
runs an act-observe loop rather than firing a blind script.

Design rule learned the hard way: everything is scoped to a *bound window*,
never to "whatever is in the foreground". The user keeps using their machine
while the agent works, so foreground focus drifts constantly. UI Automation
patterns act on a specific window and do not care about focus; synthetic
keystrokes go wherever focus happens to be and will land in the user's
browser. Prefer patterns. `key()` is the escape hatch, and it says so.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import uiautomation as auto

from . import apps

auto.SetGlobalSearchTimeout(2)

# The allowlist lives in apps.py, which also handles detection and process
# identification. A tool that can launch any executable is a remote shell
# with extra steps.

MAX_ELEMENTS = 120
MAX_DEPTH = 12

# Called with each window a launch creates, before it is snapshotted. None in
# normal use; the --demo driver sets it to place windows on the recorded screen.
on_new_window = None


class ToolError(RuntimeError):
    """Raised for conditions the agent is expected to see and react to."""


@dataclass
class Element:
    id: int
    role: str
    name: str
    rect: tuple[int, int, int, int]
    actions: list[str]
    value: str | None = None
    control: Any = field(default=None, repr=False, compare=False)

    def render(self) -> str:
        parts = [f"[{self.id}]", self.role, f'"{self.name}"']
        if self.value is not None:
            shown = self.value if len(self.value) <= 60 else self.value[:57] + "..."
            parts.append(f"value={shown!r}")
        parts.append("+".join(self.actions) if self.actions else "-")
        return " ".join(parts)


@dataclass
class Snapshot:
    window_title: str
    window_class: str
    elements: list[Element]
    window: Any = field(default=None, repr=False, compare=False)
    process: str = ""

    def render(self) -> str:
        head = f'WINDOW "{self.window_title}" ({self.window_class})'
        return "\n".join([head, *(element.render() for element in self.elements)])

    def by_id(self, element_id: int) -> Element:
        for element in self.elements:
            if element.id == element_id:
                return element
        raise ToolError(f"no element with id {element_id} in this snapshot")

    def find(self, name: str, role: str | None = None) -> Element:
        """First element whose name contains `name`, case-insensitively."""
        needle = name.lower()
        for element in self.elements:
            if needle in element.name.lower() and (role is None or element.role == role):
                return element
        raise ToolError(f"no element matching name={name!r} role={role!r}")

    def first_editable(self) -> Element:
        for element in self.elements:
            if "type" in element.actions:
                return element
        raise ToolError("no editable element in this snapshot")


_PATTERN_ACTIONS = [
    (auto.PatternId.InvokePattern, "click"),
    (auto.PatternId.ValuePattern, "type"),
    (auto.PatternId.TogglePattern, "toggle"),
    (auto.PatternId.SelectionItemPattern, "select"),
    (auto.PatternId.ExpandCollapsePattern, "expand"),
]

_KEY_NAMES = {
    "ctrl": "{Ctrl}", "control": "{Ctrl}", "alt": "{Alt}", "shift": "{Shift}",
    "win": "{Win}", "enter": "{Enter}", "return": "{Enter}", "tab": "{Tab}",
    "esc": "{Esc}", "escape": "{Esc}", "backspace": "{Back}", "delete": "{Delete}",
    "space": "{Space}", "home": "{Home}", "end": "{End}", "up": "{Up}",
    "down": "{Down}", "left": "{Left}", "right": "{Right}",
}


def _actions_for(control) -> tuple[list[str], str | None]:
    actions: list[str] = []
    value: str | None = None
    for pattern_id, label in _PATTERN_ACTIONS:
        try:
            pattern = control.GetPattern(pattern_id)
        except Exception:
            continue
        if not pattern:
            continue
        if label == "type":
            try:
                if pattern.IsReadOnly:
                    continue
                value = pattern.Value
            except Exception:
                pass
        actions.append(label)
    return actions, value


def _is_visible(control) -> bool:
    # Virtualized and collapsed controls report a zero rect. They cannot be
    # clicked, and they crowd out the elements that can.
    rect = control.BoundingRectangle
    return rect.right > rect.left and rect.bottom > rect.top


def _snapshot_or_empty(window) -> "Snapshot":
    """Snapshot a window that may have just been destroyed.

    Closing the last tab closes the window, so the snapshot that a command
    like File > Close tab returns can arrive after its target is gone. That is
    a successful close, not an error.
    """
    try:
        return snapshot(window)
    except Exception:
        return Snapshot("", "", [], window=None, process="")


def _resolve_window(window=None):
    """Return the window to act on: the bound one, else the foreground one."""
    if window is not None:
        return window
    control = auto.GetForegroundControl()
    if control is None:
        raise ToolError("no foreground window")
    root = auto.GetRootControl()
    while control is not None:
        if control.ControlType == auto.ControlType.WindowControl:
            return control
        parent = control.GetParentControl()
        if parent is None or auto.ControlsAreSame(parent, root):
            return control
        control = parent
    raise ToolError("could not resolve a top-level window")


# --- the seven tools -------------------------------------------------------


def current_window():
    """The top-level window that currently has focus, as a raw control.

    The voice bar calls this *before* it takes focus, so a follow-up command
    like "write this down" targets the app the user was actually looking at.
    """
    return _resolve_window()


def attach(title_contains: str, timeout: float = 5.0) -> Snapshot:
    """Bind to an existing top-level window by title substring."""
    needle = title_contains.lower()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for window in auto.GetRootControl().GetChildren():
            if window.ControlType == auto.ControlType.WindowControl and needle in (window.Name or "").lower():
                return snapshot(window)
        time.sleep(0.3)
    raise ToolError(f"no window with title containing {title_contains!r}")


def launch(app: str, args: list[str] | None = None, timeout: float = 20.0) -> Snapshot:
    """Start an allowlisted app and bind to the window it presents.

    Windows are matched on the owning process, never on window class:
    Chrome, Edge and every Electron app share `Chrome_WidgetWin_1`, so class
    matching would let this adopt an unrelated window and type into it.
    """
    key = apps.resolve(app) or app
    if key not in apps.REGISTRY:
        raise ToolError(
            f"{app!r} is not an app jevis can open. Available: {', '.join(apps.available_names())}"
        )
    spec = apps.get(key)
    if not spec.available:
        raise ToolError(f"{key} is not installed on this machine")

    def windows() -> list:
        found = []
        for candidate in auto.GetRootControl().GetChildren():
            if candidate.ControlType != auto.ControlType.WindowControl:
                continue
            if not (candidate.Name or "").strip():
                continue
            rect = candidate.BoundingRectangle
            if rect.right <= rect.left:
                continue
            if apps.window_process(candidate) in spec.processes:
                found.append(candidate)
        return found

    # Bind to a window this launch produced, never one that was already on
    # screen: the user may be working in it. (explorer.exe also owns the
    # desktop itself, which an "any matching window" search would adopt.)
    before = {w.NativeWindowHandle for w in windows()}
    seen = set(before)

    def ours(window) -> bool:
        """Is this new window the document we asked for?

        "New" is not enough. Starting Notepad when it is not running restores
        the previous session, so the user's own documents come back as fresh
        windows. Only a blank editor (or the file we named) is ours.
        """
        if args:
            return os.path.basename(args[0]).lower() in (window.Name or "").lower()
        if not spec.typable:
            return True
        try:
            snap = snapshot(window)
            # A restored window can have a blank tab in front of the user's
            # other tabs. Ours is a window holding one empty document.
            tabs = [e for e in snap.elements if e.role == "TabItem"]
            return len(tabs) <= 1 and not (snap.first_editable().value or "").strip()
        except Exception:
            return False

    # Single-instance apps (Calculator, a tabbed Notepad opening a file) may
    # reuse an existing window instead of making one. Give a new window a
    # moment to appear before deciding which case this is. A typable app gets
    # a second launch if the first only brought back restored documents; once
    # it is running, a bare launch opens a blank window.
    patience = min(timeout, 6.0 if before else timeout)
    for _round in range(2 if spec.typable and not args else 1):
        subprocess.Popen([*spec.argv, *(args or [])], shell=False)
        deadline = time.time() + patience
        while time.time() < deadline:
            time.sleep(0.4)
            fresh = [w for w in windows() if w.NativeWindowHandle not in seen]
            if not fresh:
                continue
            time.sleep(0.6)  # let the window finish painting its content
            for window in fresh:
                if ours(window):
                    if on_new_window is not None:
                        on_new_window(key, window)
                    return snapshot(window)
                seen.add(window.NativeWindowHandle)  # restored: leave it alone

    existing = windows()
    if existing and args:
        # The file opened as a tab in an existing window; bind only if that
        # window now shows the file we asked for.
        wanted = os.path.basename(args[0]).lower()
        for window in existing:
            if wanted in (window.Name or "").lower():
                return snapshot(window)
    if existing and spec.new_doc and not args:
        # A tabbed editor that stayed in one window: open a blank document
        # rather than adopting whatever tab the user is working in.
        return menu(snapshot(existing[0]), spec.new_doc)
    if existing and not spec.typable:
        return snapshot(existing[0])  # reusing a calculator cannot hurt anything
    raise ToolError(f"{key} did not present a new window within {patience:.0f}s")


def snapshot(window=None) -> Snapshot:
    """A compact, actionable view of a window's automation tree."""
    window = _resolve_window(window)
    elements: list[Element] = []

    def walk(control, depth: int) -> None:
        if depth > MAX_DEPTH or len(elements) >= MAX_ELEMENTS:
            return
        for child in control.GetChildren():
            if len(elements) >= MAX_ELEMENTS:
                return
            if not _is_visible(child):
                continue
            actions, value = _actions_for(child)
            name = child.Name or ""
            # Keep what the agent can act on, plus text a human would read to
            # orient. Drop pure layout containers.
            if actions or name:
                rect = child.BoundingRectangle
                elements.append(
                    Element(
                        id=len(elements) + 1,
                        role=child.ControlTypeName.replace("Control", ""),
                        name=name,
                        rect=(rect.left, rect.top, rect.right, rect.bottom),
                        actions=actions,
                        value=value,
                        control=child,
                    )
                )
            walk(child, depth + 1)

    walk(window, 0)
    return Snapshot(window.Name or "", window.ClassName or "", elements, window=window,
                    process=apps.window_process(window))


def click(snap: Snapshot, element_id: int) -> Snapshot:
    """Invoke an element through its UIA pattern. Does not require focus."""
    element = snap.by_id(element_id)
    control = element.control
    if "click" in element.actions:
        control.GetPattern(auto.PatternId.InvokePattern).Invoke()
    elif "select" in element.actions:
        control.GetPattern(auto.PatternId.SelectionItemPattern).Select()
    elif "expand" in element.actions:
        control.GetPattern(auto.PatternId.ExpandCollapsePattern).Expand()
    else:
        control.Click(simulateMove=False)  # last resort: real screen coordinates
    time.sleep(0.4)
    return snapshot(snap.window)


def type_text(snap: Snapshot, element_id: int, text: str, append: bool = False) -> Snapshot:
    """Set an element's text via ValuePattern: atomic, focus-independent."""
    element = snap.by_id(element_id)
    if "type" not in element.actions:
        raise ToolError(f"element {element_id} ({element.role}) is not editable")
    pattern = element.control.GetPattern(auto.PatternId.ValuePattern)
    pattern.SetValue((pattern.Value + text) if append else text)
    time.sleep(0.3)
    return snapshot(snap.window)


def menu(snap: Snapshot, path: list[str], timeout: float = 3.0) -> Snapshot:
    """Walk a menu by item names, e.g. ['File', 'Save'].

    Menu popups are searched from the bound window, so this works while the
    user is doing something else on another window.
    """
    window = _resolve_window(snap.window)

    def find(label: str, wait: float):
        item = window.MenuItemControl(searchDepth=30, Name=label)
        deadline = time.time() + wait
        while time.time() < deadline:
            if item.Exists(0.3):
                return item
            time.sleep(0.15)
        return None

    def act(item, is_last: bool) -> None:
        expand = item.GetPattern(auto.PatternId.ExpandCollapsePattern)
        invoke = item.GetPattern(auto.PatternId.InvokePattern)
        if not is_last and expand:
            # Expand exactly once. Calling Expand on an open XAML menu toggles
            # it shut, which is what made this flaky.
            expand.Expand()
        elif invoke:
            invoke.Invoke()
        elif expand:
            expand.Expand()
        else:
            item.Click(simulateMove=False)

    last_error = ""
    for attempt in range(3):
        # A XAML menu popup will not open unless its window is active.
        window.SetActive(waitTime=0.2)
        time.sleep(0.3 + 0.2 * attempt)
        try:
            for depth, label in enumerate(path):
                item = find(label, timeout)
                if item is None:
                    raise ToolError(f"menu item {label!r} not found (path {path[:depth + 1]})")
                act(item, depth == len(path) - 1)
                time.sleep(0.4)
            return _snapshot_or_empty(window)
        except ToolError as exc:
            last_error = str(exc)
            # Dismiss any half-open popup so the next attempt starts clean.
            try:
                auto.SendKey(auto.Keys.VK_ESCAPE)
            except Exception:
                pass
            time.sleep(0.5)
    raise ToolError(f"{last_error} (after 3 attempts)")


def key(combo: str, times: int = 1, snap: Snapshot | None = None) -> Snapshot:
    """Send a key combo. UNSAFE while the user is active: synthetic keystrokes
    go to whichever window holds focus, not to the bound window. Use `click`
    or `menu` whenever the action is reachable through a UIA pattern.
    """
    window = _resolve_window(snap.window if snap else None)
    window.SetActive(waitTime=0.3)
    time.sleep(0.3)
    parts = [part.strip().lower() for part in combo.split("+")]
    keys = "".join(_KEY_NAMES.get(part, part) for part in parts)
    for _ in range(times):
        auto.SendKeys(keys, waitTime=0.05)
    time.sleep(0.3)
    return snapshot(window)


def screenshot(path: str | None = None) -> str:
    """Pixels, on demand only. The automation tree is the primary sense."""
    from PIL import ImageGrab

    path = path or os.path.join(os.environ.get("TEMP", "."), f"jevis_{int(time.time())}.png")
    ImageGrab.grab().save(path)
    return path
