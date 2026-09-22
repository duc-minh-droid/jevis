"""The product surface: a frameless command bar with a tray icon.

Tk's stock widgets look like 1998, so almost nothing here is one. The window is
borderless and its background is keyed out via `-transparentcolor`, which is
what gives real rounded corners on Windows. The card, the status dot, the
suggestion chips, the progress underline and every label are drawn on a Canvas.

One key in, one key out:
  ctrl+alt+j   summon (bar appears already focused, ready for dictation)
  Enter        run
  Tab          accept the first suggestion
  Up / Down    walk back through commands already run
  Esc          dismiss

The bar does not hide on focus loss. Dictation tools briefly take focus while
they transcribe, and auto-hiding would yank the field out from under them
mid-sentence.
"""
from __future__ import annotations

import ctypes
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont

from . import agent, brain, skills, tools


def _enable_dpi_awareness() -> None:
    """Without this, Tk renders blurry on any scaled display."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


HOTKEY = "ctrl+alt+j"

# The keyed-out colour. Nothing in the design may use it.
CHROMA = "#ff00ff"

CARD = "#0b0e15"
EDGE = "#1e2532"
FIELD = "#131822"
FIELD_EDGE = "#222a39"
CHIP = "#141a26"
CHIP_EDGE = "#232c3d"
TEXT = "#f2f4f9"
GHOST = "#525b6d"
MUTED = "#7b8599"
ACCENT = "#5b9dff"
GOOD = "#34d399"
BAD = "#fb7185"

WIDTH, HEIGHT = 720, 186
RADIUS = 22
PAD = 22
HEAD_Y = 24
FIELD_TOP = 48
FIELD_H = 54
CHIP_Y = 118
CHIP_H = 26
STATUS_Y = HEIGHT - 24

PLACEHOLDER = "Speak or type a command"
MAX_HISTORY = 30


def _lerp(start: str, end: str, t: float) -> str:
    """Blend two hex colours. Tk has no alpha, so fades are done by hand."""
    a = tuple(int(start[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(end[i : i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _round_rect(canvas: tk.Canvas, x0, y0, x1, y1, r, **kwargs):
    points = [
        x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
        x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
        x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=36, **kwargs)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class CommandBar:
    def __init__(self) -> None:
        self.target_window = None
        self.events: queue.Queue[tuple[str, str, bool]] = queue.Queue()
        self.phase = 0.0
        self.running = False
        self.chips: list[int] = []
        self.suggestions: list[str] = []
        self.history: list[str] = []
        self.history_index = 0

        self.root = tk.Tk()
        self.root.title("jevis")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.transparent = True
        try:
            self.root.configure(bg=CHROMA)
            self.root.attributes("-transparentcolor", CHROMA)
        except tk.TclError:
            self.transparent = False
            self.root.configure(bg=CARD)

        self.home_x = (self.root.winfo_screenwidth() - WIDTH) // 2
        self.home_y = 160
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{self.home_x}+{self.home_y}")

        back = CHROMA if self.transparent else CARD
        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT,
                                highlightthickness=0, bg=back)
        self.canvas.pack(fill="both", expand=True)

        _round_rect(self.canvas, 1, 1, WIDTH - 1, HEIGHT - 1, RADIUS,
                    fill=CARD, outline=EDGE, width=1)
        _round_rect(self.canvas, PAD, FIELD_TOP, WIDTH - PAD, FIELD_TOP + FIELD_H, 13,
                    fill=FIELD, outline=FIELD_EDGE, width=1)

        self.f_mark = tkfont.Font(family="Segoe UI Semibold", size=10)
        self.f_meta = tkfont.Font(family="Segoe UI", size=9)
        self.f_chip = tkfont.Font(family="Segoe UI", size=9)
        self.f_entry = tkfont.Font(family="Segoe UI", size=16)

        self.dot = self.canvas.create_oval(PAD, HEAD_Y - 5, PAD + 10, HEAD_Y + 5,
                                           fill=MUTED, outline="")
        self.canvas.create_text(PAD + 20, HEAD_Y, text="jevis", anchor="w",
                                fill=TEXT, font=self.f_mark)
        self.target_text = self.canvas.create_text(
            WIDTH - PAD, HEAD_Y, text="", anchor="e", fill=GHOST, font=self.f_meta)

        # The one stock widget, because text input needs a real caret.
        self.entry = tk.Entry(
            self.canvas, font=self.f_entry, bg=FIELD, fg=TEXT, insertbackground=ACCENT,
            relief="flat", borderwidth=0, highlightthickness=0,
            disabledbackground=FIELD, disabledforeground=MUTED,
        )
        self.canvas.create_window(PAD + 17, FIELD_TOP + 11, anchor="nw",
                                  window=self.entry, width=WIDTH - PAD * 2 - 34,
                                  height=FIELD_H - 22)
        self.entry.bind("<Return>", self.submit)
        self.entry.bind("<Escape>", lambda _e: self.hide())
        self.entry.bind("<Tab>", self._accept_suggestion)
        self.entry.bind("<Up>", self._history_back)
        self.entry.bind("<Down>", self._history_forward)
        self.entry.bind("<KeyRelease>", self._on_type)

        self.bar = self.canvas.create_rectangle(
            PAD, FIELD_TOP + FIELD_H - 2, PAD, FIELD_TOP + FIELD_H,
            fill=ACCENT, outline="")

        self.status = self.canvas.create_text(
            PAD + 1, STATUS_Y, text="", anchor="w", fill=MUTED, font=self.f_meta)
        self.hints = self.canvas.create_text(
            WIDTH - PAD, STATUS_Y, text="Tab complete    Enter run    Esc close",
            anchor="e", fill=GHOST, font=self.f_meta)

        self.root.withdraw()
        self.root.after(60, self._drain)

    # --- painting ----------------------------------------------------------

    def _say(self, text: str, colour: str = MUTED) -> None:
        self.canvas.itemconfigure(self.status, text=text, fill=colour)
        # A result line is long and would collide with the key hints, so the
        # hints yield while one is showing.
        busy = colour in (ACCENT, GOOD, BAD)
        self.canvas.itemconfigure(self.hints, state="hidden" if busy else "normal")

    def _dot(self, colour: str) -> None:
        self.canvas.itemconfigure(self.dot, fill=colour)

    def _underline(self, fraction: float, colour: str = ACCENT) -> None:
        width = max(0.0, min(1.0, fraction)) * (WIDTH - PAD * 2)
        self.canvas.coords(self.bar, PAD, FIELD_TOP + FIELD_H - 2,
                           PAD + width, FIELD_TOP + FIELD_H)
        self.canvas.itemconfigure(self.bar, fill=colour)

    def _render_chips(self, items: list[str]) -> None:
        for item in self.chips:
            self.canvas.delete(item)
        self.chips = []
        self.suggestions = []
        x = PAD
        for text in items:
            width = self.f_chip.measure(text) + 24
            if x + width > WIDTH - PAD:
                break
            box = _round_rect(self.canvas, x, CHIP_Y, x + width, CHIP_Y + CHIP_H, 13,
                              fill=CHIP, outline=CHIP_EDGE, width=1)
            label = self.canvas.create_text(x + 12, CHIP_Y + CHIP_H // 2, text=text,
                                            anchor="w", fill=MUTED, font=self.f_chip)
            self.chips += [box, label]
            self.suggestions.append(text)
            x += width + 8

    def _text(self) -> str:
        return self.entry.get().strip()

    # --- input -------------------------------------------------------------

    def _on_type(self, _event=None) -> None:
        instruction = self._text()
        if not instruction:
            self._dot(MUTED)
            self._underline(0)
            self._say(PLACEHOLDER, GHOST)
            self._render_chips(skills.suggest(""))
            return

        self._dot(ACCENT)
        if skills.match(instruction):
            self._say("deterministic   no model runs", GOOD)
            self._render_chips([])
        else:
            self._say("model-planned", MUTED)
            self._render_chips(skills.suggest(instruction))

    def _accept_suggestion(self, _event=None) -> str:
        if self.suggestions:
            self.entry.delete(0, "end")
            self.entry.insert(0, self.suggestions[0])
            self._on_type()
        return "break"  # keep Tab from moving focus out of the field

    def _history_back(self, _event=None) -> str:
        if self.history and self.history_index > 0:
            self.history_index -= 1
            self.entry.delete(0, "end")
            self.entry.insert(0, self.history[self.history_index])
            self._on_type()
        return "break"

    def _history_forward(self, _event=None) -> str:
        if not self.history:
            return "break"
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.entry.delete(0, "end")
            self.entry.insert(0, self.history[self.history_index])
        else:
            self.history_index = len(self.history)
            self.entry.delete(0, "end")
        self._on_type()
        return "break"

    def _animate(self) -> None:
        if not self.running:
            return
        self.phase = (self.phase + 0.055) % 1.0
        swing = self.phase * 2 if self.phase < 0.5 else (1 - self.phase) * 2
        self._dot(_lerp(EDGE, ACCENT, swing))
        self._underline(0.12 + swing * 0.88)
        self.root.after(35, self._animate)

    # --- lifecycle ---------------------------------------------------------

    def show(self) -> None:
        # Capture the outgoing window before taking focus, so a command like
        # "write this down" targets the app the user was looking at.
        try:
            self.target_window = tools.current_window()
            label = (self.target_window.Name or "").strip()
        except Exception:
            self.target_window = None
            label = ""
        self.canvas.itemconfigure(self.target_text, text=_shorten(label or "no window", 46))

        self.running = False
        self.entry.configure(state="normal")
        self.entry.delete(0, "end")
        self.history_index = len(self.history)
        self._dot(MUTED)
        self._underline(0)
        self._say(PLACEHOLDER, GHOST)
        self._render_chips(skills.suggest(""))

        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.0)
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{self.home_x}+{self.home_y + 14}")
        self.root.focus_force()
        self.entry.focus_set()
        self._enter(0)

    def _enter(self, step: int) -> None:
        """Fade up while sliding the last 14px into place."""
        total = 7
        if step > total:
            return
        t = step / total
        eased = 1 - (1 - t) ** 3
        try:
            self.root.attributes("-alpha", eased)
            self.root.geometry(
                f"{WIDTH}x{HEIGHT}+{self.home_x}+{self.home_y + round(14 * (1 - eased))}")
        except tk.TclError:
            return
        self.root.after(12, lambda: self._enter(step + 1))

    def hide(self) -> None:
        self.running = False
        self._fade_out(0)

    def _fade_out(self, step: int) -> None:
        total = 5
        try:
            if step > total:
                self.root.withdraw()
                return
            self.root.attributes("-alpha", 1 - step / total)
        except tk.TclError:
            return
        self.root.after(12, lambda: self._fade_out(step + 1))

    def _drain(self) -> None:
        """Pump worker results onto the Tk thread. Tk is not thread-safe."""
        try:
            while True:
                text, colour, close = self.events.get_nowait()
                self.running = False
                self._dot(colour)
                self._underline(1.0, colour)
                self._say(text, colour)
                self.entry.configure(state="normal")
                if close:
                    self.root.after(1500, self.hide)
        except queue.Empty:
            pass
        self.root.after(60, self._drain)

    # --- running -----------------------------------------------------------

    def submit(self, _event=None) -> None:
        instruction = self._text()
        if not instruction or self.running:
            return
        self.history = [h for h in self.history if h != instruction][-MAX_HISTORY:]
        self.history.append(instruction)
        self.history_index = len(self.history)

        self.entry.configure(state="disabled")
        self._render_chips([])
        self.running = True
        self.phase = 0.0
        self._animate()
        self._say(f"running   {_shorten(instruction, 58)}", ACCENT)

        window = self.target_window
        threading.Thread(target=self._work, args=(instruction, window), daemon=True).start()

    def _work(self, instruction: str, window) -> None:
        try:
            result = agent.run(instruction, window=window, verbose=False)
            if result.ok:
                how = result.skill or result.tier
                self.events.put((f"done   {how}   {result.elapsed:.1f}s", GOOD, True))
            else:
                self.events.put((f"failed   {_shorten(result.error, 92)}", BAD, False))
        except Exception as exc:
            self.events.put((f"error   {_shorten(f'{type(exc).__name__}: {exc}', 92)}", BAD, False))

    # --- tray --------------------------------------------------------------

    def _tray(self):
        import pystray
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((5, 5, 59, 59), fill="#0b0e15", outline="#5b9dff", width=4)
        draw.ellipse((26, 26, 38, 38), fill="#5b9dff")

        menu = pystray.Menu(
            pystray.MenuItem(f"Show  ({HOTKEY})", lambda: self.root.after(0, self.show),
                             default=True),
            pystray.MenuItem("Quit", self._quit),
        )
        return pystray.Icon("jevis", image, "jevis", menu)

    def _quit(self, icon=None, _item=None) -> None:
        if icon:
            icon.stop()
        self.root.after(0, self.root.destroy)

    # --- entry point -------------------------------------------------------

    def run(self) -> None:
        brain.load_env()
        try:
            import keyboard

            keyboard.add_hotkey(HOTKEY, lambda: self.root.after(0, self.show))
            print(f"jevis ready   {HOTKEY} to summon   tray icon to quit")
        except Exception as exc:
            print(f"global hotkey unavailable ({exc}); opening the bar instead.")
            self.root.after(200, self.show)

        try:
            threading.Thread(target=self._tray().run, daemon=True).start()
        except Exception as exc:
            print(f"tray icon unavailable ({exc}); running without it.")

        self.root.mainloop()


def main() -> int:
    _enable_dpi_awareness()
    CommandBar().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
