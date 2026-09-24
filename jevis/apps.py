"""Which apps jevis may drive, and how to recognise their windows.

Window class is not enough to identify an app. `Chrome_WidgetWin_1` is Chrome,
Edge, and every Electron app on the machine — including the Claude desktop
client. Binding by class alone would let "open my browser" adopt a chat window
and type into it. So each app declares the process executables that own its
windows, and matching is done on those.

Apps are detected at import. An app that is not installed is still listed, but
marked unavailable, so the agent can say "not installed" instead of silently
doing something else.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import shutil
import winreg
from dataclasses import dataclass, field

_kernel32 = ctypes.windll.kernel32
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def process_name(pid: int) -> str:
    """Executable basename owning `pid`, lowercased. Empty when unknowable."""
    if not pid:
        return ""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(600)
        size = wintypes.DWORD(600)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        _kernel32.CloseHandle(handle)


_FRAME_HOST = "applicationframehost.exe"


def window_process(window) -> str:
    """The process that really owns a top-level window.

    Store (UWP) apps such as Calculator present their top-level window through
    ApplicationFrameHost.exe; the app's own process owns a child CoreWindow.
    Matching the frame host would match every Store app at once, so look
    through it to the child.
    """
    name = process_name(window.ProcessId)
    if name == _FRAME_HOST:
        try:
            for child in window.GetChildren():
                owner = process_name(child.ProcessId)
                if owner and owner != _FRAME_HOST:
                    return owner
        except Exception:
            pass
    return name


def default_browser() -> tuple[str, str] | None:
    """(executable path, process name) of the registered https handler."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        ) as key:
            progid = winreg.QueryValueEx(key, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command") as key:
            command = winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None
    path = command.split('"')[1] if command.startswith('"') else command.split(" ")[0]
    if not os.path.exists(path):
        return None
    return path, os.path.basename(path).lower()


@dataclass(frozen=True)
class App:
    name: str
    argv: list[str]
    # Executables that own this app's windows. Matching is done on these.
    processes: frozenset[str]
    # Menu path that opens a blank document, for tabbed apps that would
    # otherwise hand back whatever tab the user is working in.
    new_doc: list[str] | None = None
    # False when the agent must not type into it; a terminal executes text.
    typable: bool = True
    note: str = ""

    @property
    def available(self) -> bool:
        return bool(self.argv) and (
            os.path.exists(self.argv[0]) or shutil.which(self.argv[0]) is not None
        )


def _build() -> dict[str, App]:
    registry: dict[str, App] = {
        "notepad": App("notepad", ["notepad.exe"], frozenset({"notepad.exe"}),
                       new_doc=["File", "New tab"]),
        "calc": App("calc", ["calc.exe"],
                    frozenset({"calculatorapp.exe", "calculator.exe"}), typable=False),
        "mspaint": App("mspaint", ["mspaint.exe"], frozenset({"mspaint.exe"}), typable=False),
        "explorer": App("explorer", ["explorer.exe"], frozenset({"explorer.exe"}),
                        typable=False),
    }

    browser = default_browser()
    if browser:
        path, process = browser
        registry["browser"] = App("browser", [path], frozenset({process}), typable=False,
                                  note=f"default browser ({process})")

    terminal = shutil.which("wt.exe")
    if terminal:
        registry["terminal"] = App(
            "terminal", [terminal], frozenset({"windowsterminal.exe"}), typable=False,
            note="Windows Terminal. Typing is disabled: a terminal executes what it receives.",
        )
    else:
        powershell = shutil.which("powershell.exe")
        if powershell:
            registry["terminal"] = App(
                "terminal", [powershell], frozenset({"powershell.exe"}), typable=False,
                note="PowerShell. Typing is disabled: a terminal executes what it receives.",
            )
    return registry


REGISTRY: dict[str, App] = _build()

# Aliases people actually say, mapped to registry keys.
ALIASES = {
    "notepad": "notepad", "note pad": "notepad", "notepad++": "notepad",
    "calculator": "calc", "calc": "calc",
    "paint": "mspaint", "ms paint": "mspaint", "microsoft paint": "mspaint",
    "browser": "browser", "my browser": "browser", "the browser": "browser",
    "my default browser": "browser", "default browser": "browser",
    "chrome": "browser", "google chrome": "browser", "edge": "browser",
    "internet": "browser", "web browser": "browser",
    "terminal": "terminal", "my terminal": "terminal", "the terminal": "terminal",
    "command prompt": "terminal", "command line": "terminal", "console": "terminal",
    "powershell": "terminal", "windows terminal": "terminal", "cmd": "terminal",
    "explorer": "explorer", "file explorer": "explorer", "files": "explorer",
    "windows explorer": "explorer", "my files": "explorer",
}


def resolve(spoken: str) -> str | None:
    """Map a spoken app name to a registry key, or None if unknown."""
    return ALIASES.get(spoken.strip().lower().rstrip(".!?"))


def get(key: str) -> App:
    return REGISTRY[key]


def available_names() -> list[str]:
    return sorted(name for name, app in REGISTRY.items() if app.available)
