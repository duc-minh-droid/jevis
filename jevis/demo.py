"""Scripted run of the real command bar, for recordings and for a quick look.

    python -m jevis --demo                      the default script
    python -m jevis --demo "open calculator"    your own commands
    python -m jevis --demo --speak              also play the TTS audio aloud
    python -m jevis --demo --record out.mp4     capture the primary screen too

Each command is synthesised with the built-in Windows voice. Its amplitude
envelope drives the waveform and the transcript lands in the field in step with
it, which is what a dictation tool typing into the bar looks like. Then the bar
runs it through the normal agent path: same matcher, same tools, same
verification. Nothing on screen is mocked.

Every command is captioned at the bottom of the screen. A plain backdrop covers
the primary monitor first, so a recording shows the apps jevis drives and not
whatever else the user has open; recording only starts once it is up.
Afterwards the driver closes the windows it opened, by handle. A window that
existed before the demo started is never closed or typed into.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

from PIL import Image, ImageDraw, ImageFilter

from . import apps, overlay, skills, tools, ui, voice

SCRIPT = [
    "open calculator",
    "open notepad and write hello from jevis",
    "new tab",
    "write it reads the UI Automation tree, not pixels",
]


def _top_windows() -> dict[int, object]:
    import uiautomation as auto

    return {w.NativeWindowHandle: w for w in auto.GetRootControl().GetChildren()
            if w.ControlType == auto.ControlType.WindowControl}


def _ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def render_caption(text: str, sub: str) -> Image.Image:
    title_font, sub_font = overlay._font(19, bold=True), overlay._font(12)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    quoted = f"“{text}”"
    w = int(max(probe.textlength(quoted, font=title_font),
                probe.textlength(sub, font=sub_font))) + 96
    h, pad = 70, 20
    image = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + 5, pad + w, pad + h + 5), 20,
                                             fill=(0, 0, 0, 140))
    image = Image.alpha_composite(image, shadow.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((pad, pad, pad + w, pad + h), 20, fill=(11, 14, 21, 240),
                        outline=(38, 46, 62, 255), width=1)
    blue = (91, 157, 255, 255)
    cx, cy = pad + 38, pad + h // 2
    d.ellipse((cx - 19, cy - 19, cx + 19, cy + 19), fill=(20, 32, 56, 255))
    d.rounded_rectangle((cx - 5, cy - 11, cx + 5, cy + 4), 5, fill=blue)
    d.arc((cx - 9, cy - 6, cx + 9, cy + 9), 0, 180, fill=blue, width=2)
    d.line((cx, cy + 9, cx, cy + 13), fill=blue, width=2)
    d.text((pad + 70, pad + 25), quoted, font=title_font, fill=(242, 244, 249, 255), anchor="lm")
    d.text((pad + 70, pad + 50), sub, font=sub_font, fill=(123, 133, 153, 255), anchor="lm")
    return image


class Caption:
    """The spoken command, bottom centre, for anyone watching without sound."""

    def __init__(self, root) -> None:
        self.layer = overlay.Layer(root)
        self.shown = 0.0
        self.leaving = 0.0
        self.x = self.y = 0

    def show(self, text: str, sub: str) -> None:
        image = render_caption(text, sub)
        left, _top, right, bottom = REGION
        self.x = (left + right - image.width) // 2
        self.y = bottom - image.height + 4
        self.layer.paint(image, self.x, self.y + 12, 0)
        self.shown, self.leaving = time.time(), 0.0

    def hide(self) -> None:
        if self.shown and not self.leaving:
            self.leaving = time.time()

    def tick(self) -> None:
        if not self.shown:
            return
        if self.leaving:
            t = min(1.0, (time.time() - self.leaving) / 0.3)
            self.layer.set(self.x, self.y + round(8 * t), 255 * (1 - t))
            if t >= 1:
                self.shown = 0.0
            return
        t = ui._ease((time.time() - self.shown) / 0.3)
        self.layer.set(self.x, self.y + round(12 * (1 - t)), 255 * t)


def _backdrop(root):
    """A plain sheet over the primary monitor, below everything the demo opens."""
    import tkinter as tk

    from PIL import ImageTk

    w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    sheet = tk.Toplevel(root)
    sheet.overrideredirect(True)
    sheet.geometry(f"{w}x{h}+0+0")
    image = Image.new("RGB", (w, h))
    top, bottom = (24, 29, 41), (9, 11, 17)
    draw = ImageDraw.Draw(image)
    for y in range(h):
        t = y / h
        draw.line((0, y, w, y), fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    photo = ImageTk.PhotoImage(image)
    label = tk.Label(sheet, image=photo, borderwidth=0)
    label.image = photo
    label.pack()
    sheet.attributes("-topmost", True)   # cover everything first ...
    sheet.update()
    sheet.attributes("-topmost", False)  # ... then let launched apps come above it
    return sheet


# The recorded part of the primary screen (left, top, right, bottom), and
# where each launched app lands inside it as (x, y, width, height).
REGION = (240, 120, 1680, 930)
PLACES = {"calc": (292, 318, 336, 492), "notepad": (668, 420, 960, 396)}


def _place(app: str, window) -> None:
    """Move a window the demo just launched. Never applied to existing ones."""
    if app not in PLACES:
        return
    user32 = ctypes.windll.user32
    hwnd = window.NativeWindowHandle
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE, in case it opened maximised
    x, y, w, h = PLACES[app]
    # Move first, size second: a window that opened on a monitor with a
    # different scale factor rescales itself when it crosses over, which would
    # undo a size set in the same call.
    user32.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010)  # NOSIZE
    time.sleep(0.2)
    user32.SetWindowPos(hwnd, 0, x, y, w, h, 0x0004 | 0x0010)  # NOZORDER | NOACTIVATE
    time.sleep(0.25)


def _typed_by(commands: list[str]) -> set[str]:
    """Every text the script will type, so cleanup can recognise its own tabs."""
    texts = {""}
    for command in commands:
        matched = skills.match(command)
        for step in (matched[1] if matched else []):
            if step.get("tool") == "type_text":
                texts.add(" ".join(step.get("text", "").split()))
    return texts


def _notepad_state() -> tuple:
    import uiautomation as auto

    state = []
    for w in auto.GetRootControl().GetChildren():
        if apps.window_process(w) in apps.get("notepad").processes:
            state.append((w.NativeWindowHandle, w.Name))
    return tuple(sorted(state))


def _prewarm_notepad(commands: list[str]) -> bool:
    """Start Notepad before anything is recorded, and wait for it to settle.

    When Notepad is not running, its first launch restores the user's previous
    session: their tabs come back as new windows, over several seconds. Doing
    that here, before the backdrop goes up and before the demo's own launches,
    means those windows are already present, sit under the backdrop, and are
    never candidates for a launch. A later bare launch opens a clean window.
    """
    if not any("notepad" in c.lower() for c in commands) or _notepad_state():
        return False
    print("starting Notepad first so any session restore happens off camera ...")
    subprocess.Popen(["notepad.exe"])
    last, stable_since, deadline = None, time.time(), time.time() + 25
    while time.time() < deadline:
        time.sleep(0.5)
        now = _notepad_state()
        if now != last:
            last, stable_since = now, time.time()
        elif now and time.time() - stable_since > 4:
            break
    return True


def _alive(window) -> bool:
    try:
        return bool(window.Exists(0.2))
    except Exception:  # a window that just closed can fail the check itself
        return False


def _close_notepad_window(window, allowed: set[str]) -> None:
    """Empty each tab, then close it. Only called on windows the demo made,
    and only for tabs holding text the demo itself typed."""
    import uiautomation as auto

    for _ in range(8):
        try:
            if not _alive(window):
                return
            snap = tools.snapshot(window)
            editor = snap.first_editable()
            if " ".join((editor.value or "").split()) not in allowed:
                print(f"cleanup: left {snap.window_title!r} open, it holds text the demo did not type")
                return
            editor.control.GetPattern(auto.PatternId.ValuePattern).SetValue("")
            time.sleep(0.2)
            tools.menu(snap, ["File", "Close tab"])
            time.sleep(0.4)
        except Exception as exc:
            # Closing the last tab closes the window mid-call; that is success.
            if _alive(window):
                print(f"cleanup: {type(exc).__name__}: {exc}")
            return


def cleanup(before: set[int], bound: set[int], allowed: set[str]) -> None:
    """Close windows the demo created. Anything present beforehand stays."""
    windows = _top_windows()
    for handle in bound - before:
        window = windows.get(handle)
        if window is None:
            continue
        owner = apps.window_process(window)
        if owner in apps.get("notepad").processes:
            _close_notepad_window(window, allowed)
        else:
            _post_close(window)


def write_traces(path: str, traces: list[dict]) -> None:
    """The run as data, plus the matcher's patterns, for web/index.html."""
    import json

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "skills": [{"name": s.name, "pattern": s.pattern.pattern} for s in skills.SKILLS],
        "apps": apps.available_names(),
        "traces": traces,
    }
    for trace in payload["traces"]:
        for event in trace["events"]:
            event.pop("handle", None)   # meaningless outside this session
    body = json.dumps(payload, indent=1, ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as f:
        if path.endswith(".js"):
            f.write(f"window.JEVIS_TRACES = {body};\n")
        else:
            f.write(body)
    print(f"wrote {path}")


def _post_close(window) -> None:
    """Ask a window to close without waiting on it (UIA Close can block
    while the owning process shuts down)."""
    ctypes.windll.user32.PostMessageW(window.NativeWindowHandle, 0x0010, 0, 0)  # WM_CLOSE


def _close_restored(handles: set[int]) -> None:
    """Close windows that only appeared because the prewarm started Notepad.

    Closing the window (not its tabs) is what the user would have done: with
    session restore on, Notepad keeps every tab for next time. A window whose
    title shows unsaved changes is left alone.
    """
    windows = _top_windows()
    for handle in handles:
        window = windows.get(handle)
        if window is None or (window.Name or "").startswith("*"):
            continue
        _post_close(window)


def main(argv: list[str]) -> int:
    speak = "--speak" in argv
    plain = "--no-backdrop" in argv
    def option(name: str) -> str | None:
        nonlocal argv
        if name not in argv:
            return None
        at = argv.index(name)
        value = os.path.abspath(argv[at + 1])
        argv = argv[:at] + argv[at + 2:]
        return value

    record = option("--record")
    trace_out = option("--trace-out")
    commands = [a for a in argv if not a.startswith("--")] or SCRIPT

    for command in commands:
        if not skills.match(command):
            print(f"note: {command!r} is not deterministic and will reach the model tier")

    print("synthesising commands with Windows TTS ...")
    envelopes = []
    for command in commands:
        path = voice.speak_to_wav(command)
        envelopes.append((path, voice.envelope_of(path)))

    untouched = set(_top_windows())
    prewarmed = _prewarm_notepad(commands)
    before = set(_top_windows())
    # Windows Notepad restored from the user's last session when the prewarm
    # started it. They are the user's; the demo only puts them back to sleep,
    # and closing a window (not its tabs) keeps the session as it was.
    restored = {h for h, w in _top_windows().items()
                if prewarmed and h not in untouched
                and apps.window_process(w) in apps.get("notepad").processes}
    tools.on_new_window = _place
    bar = ui.CommandBar()
    sheet = None if plain else _backdrop(bar.root)
    caption = Caption(bar.root)
    bar.toast.area = REGION
    state = {"i": -1, "window": None, "bound": set(), "recorder": None, "traces": []}

    if record:
        ffmpeg = _ffmpeg()
        if ffmpeg is None:
            print("--record needs imageio-ffmpeg; recording skipped")
        else:
            left, top, right, bottom = REGION
            state["recorder"] = subprocess.Popen(
                [ffmpeg, "-y", "-loglevel", "error", "-f", "gdigrab", "-framerate", "30",
                 "-draw_mouse", "0", "-offset_x", str(left), "-offset_y", str(top),
                 "-video_size", f"{right - left}x{bottom - top}", "-i", "desktop", "-c:v", "libx264",
                 "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", record],
                stdin=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    # Captions tick with the bar's frame loop.
    toast_tick = bar.toast.tick

    def tick() -> None:
        toast_tick()
        caption.tick()

    bar.toast.tick = tick

    def next_command() -> None:
        state["i"] += 1
        i = state["i"]
        if i >= len(commands):
            caption.hide()
            bar.root.after(3000, finish)
            return
        command = commands[i]
        bar.show()
        if not command.lower().startswith("open") and state["window"] is not None:
            # A follow-up like "new tab" acts on the window the previous
            # command produced, never on whatever else happens to be focused.
            bar.target_window = state["window"]
            bar.canvas.itemconfigure(bar.target_text,
                                     text=ui._shorten(state["window"].Name or "", 46))
        else:
            # "open ..." binds to the window it launches; do not show or
            # snapshot whatever the user had focused.
            bar.target_window = None
            bar.canvas.itemconfigure(bar.target_text, text="desktop")
        tier = "deterministic tier, no model" if skills.match(command) else "model tier"
        caption.show(command, f"spoken with Windows TTS  ·  {tier}  ·  {i + 1} of {len(commands)}")
        path, env = envelopes[i]
        if speak:
            voice.play(path)
        bar.root.after(520, lambda: bar.dictate(command, env, lambda: bar.submit(close=True)))

    def on_result(data: dict) -> None:
        if bar.last_trace is not None:
            env = envelopes[state["i"]][1]
            # The voice as it was heard, at 30 fps, so the inspector's
            # waveform is the real envelope rather than a decoration.
            bar.last_trace["voice"] = {
                "fps": env.fps // 2,
                "levels": [round(v, 2) for v in env.levels[::2]],
            }
            state["traces"].append(bar.last_trace)
            bar.last_trace = None
        if bar.bound_handle:
            state["bound"].add(bar.bound_handle)
            state["window"] = _top_windows().get(bar.bound_handle, state["window"])
            bar.bound_handle = 0
        if not data["ok"]:
            print(f"{commands[state['i']]!r} failed: {data['error']}")
            bar.root.after(1800, bar.hide)
        bar.root.after(1700, caption.hide)
        bar.root.after(2700, next_command)

    def finish() -> None:
        recorder = state["recorder"]
        if recorder is not None:
            recorder.communicate(b"q", timeout=20)
        if trace_out:
            write_traces(trace_out, state["traces"])
        if sheet is not None:
            sheet.destroy()
        bar.root.withdraw()
        bar.root.update()
        try:
            cleanup(before, state["bound"], _typed_by(commands))
            time.sleep(0.8)
            _close_restored(restored)
        finally:
            bar.root.destroy()

    bar.on_result = on_result
    bar.root.after(900, next_command)
    bar.root.mainloop()
    return 0


if __name__ == "__main__":
    ui._enable_dpi_awareness()
    raise SystemExit(main(sys.argv[1:]))
