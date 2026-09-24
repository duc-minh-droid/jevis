"""The product surface: a frameless command bar with a tray icon.

Tk's stock widgets look like 1998, so almost nothing here is one. The window is
borderless and its background is keyed out via `-transparentcolor`, which is
what gives real rounded corners on Windows. The card, the orb, the waveform,
the plan rows and every label are drawn on a Canvas and driven by one frame
loop. The glow around the element being acted on and the completion toast are
per-pixel-alpha layered windows (see overlay.py).

The bar moves through five states, and each has its own motion:

  ready       orb breathes slowly, suggestions offered
  listening   waveform and orb follow the input level; the transcript lands
  thinking    orb ring spins while a tier is chosen
  acting      the plan unfolds as rows that tick off; the target UIA element
              glows on screen, from its BoundingRectangle
  done        ring ripples out, underline fills, a toast slides in

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
import math
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

from . import agent, brain, overlay, skills, tools, voice


def _enable_dpi_awareness() -> None:
    """Without this, Tk renders blurry on any scaled display."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system aware
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
THINK = "#a78bfa"
GOOD = "#34d399"
BAD = "#fb7185"

WIDTH = 720
RADIUS = 22
PAD = 22
HEAD_Y = 26
FIELD_TOP = 50
FIELD_H = 54
CHIP_Y = 120
CHIP_H = 26
ROW_Y = 122
ROW_H = 30
MAX_ROWS = 8
BASE_H = 188                                   # ready/listening card height
HEIGHT = ROW_Y + MAX_ROWS * ROW_H + 50         # window height; the rest is keyed out

WAVE_BARS = 26
WAVE_W = 4
WAVE_GAP = 3
WAVE_RIGHT = WIDTH - PAD - 18

PLACEHOLDER = "Speak or type a command"
MAX_HISTORY = 30
FRAME_MS = 16

# Minimum time each event stays on screen. The deterministic tier is fast
# enough that without this a two-step plan would tick off in one frame; this
# paces the display only, never the agent.
DWELL = {"match": 0.30, "step": 0.18, "target": 0.10, "verify": 0.32, "attempt": 0.4}


def _lerp(start: str, end: str, t: float) -> str:
    """Blend two hex colours. Tk has no alpha, so fades are done by hand."""
    t = max(0.0, min(1.0, t))
    a = tuple(int(start[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(end[i : i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _rr_points(x0, y0, x1, y1, r):
    return [
        x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
        x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
        x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
    ]


def _round_rect(canvas: tk.Canvas, x0, y0, x1, y1, r, **kwargs):
    return canvas.create_polygon(_rr_points(x0, y0, x1, y1, r), smooth=True,
                                 splinesteps=36, **kwargs)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


class Row:
    """One plan step: a glyph that becomes a spinner, then a drawn check."""

    def __init__(self, bar: "CommandBar", index: int, label: str) -> None:
        self.bar = bar
        self.index = index
        self.label = label
        self.state = "pending"          # pending | running | ok | fail
        self.born = time.time() + 0.06 * index
        self.changed = self.born
        self.detail = ""
        c = bar.canvas
        y = ROW_Y + (index - 1) * ROW_H + ROW_H // 2
        self.y = y
        gx = PAD + 12
        self.ring = c.create_oval(gx - 7, y - 7, gx + 7, y + 7, outline=CARD, width=2)
        self.arc = c.create_arc(gx - 7, y - 7, gx + 7, y + 7, start=90, extent=0,
                                style="arc", outline=CARD, width=2)
        self.check = c.create_line(gx, y, gx, y, fill=CARD, width=2, capstyle="round",
                                   joinstyle="round")
        self.text = c.create_text(PAD + 32, y, text=label, anchor="w", fill=CARD,
                                  font=bar.f_row)
        self.info = c.create_text(WIDTH - PAD - 4, y, text="", anchor="e", fill=CARD,
                                  font=bar.f_meta)
        self.items = [self.ring, self.arc, self.check, self.text, self.info]

    def set(self, state: str, detail: str | None = None) -> None:
        if state != self.state:
            self.state = state
            self.changed = time.time()
        if detail is not None:
            self.detail = detail

    def draw(self, now: float) -> None:
        c = self.bar.canvas
        fade = _ease((now - self.born) / 0.25)
        gx, y = PAD + 12, self.y
        slide = round(10 * (1 - fade))
        c.coords(self.text, PAD + 32 + slide, y)
        since = now - self.changed

        if self.state == "pending":
            c.itemconfigure(self.ring, outline=_lerp(CARD, EDGE, fade), fill="")
            c.itemconfigure(self.arc, outline=CARD, extent=0)
            c.coords(self.check, gx, y, gx, y)
            c.itemconfigure(self.check, fill=CARD)
            c.itemconfigure(self.text, fill=_lerp(CARD, GHOST, fade))
        elif self.state == "running":
            c.itemconfigure(self.ring, outline=EDGE, fill="")
            c.itemconfigure(self.arc, outline=ACCENT, start=(90 - now * 420) % 360, extent=110)
            c.itemconfigure(self.text, fill=_lerp(GHOST, TEXT, since / 0.2))
        else:
            colour = GOOD if self.state == "ok" else BAD
            pop = _ease(since / 0.22)
            r = 7 + 2 * math.sin(math.pi * min(1.0, since / 0.3))
            c.coords(self.ring, gx - r, y - r, gx + r, y + r)
            c.itemconfigure(self.ring, outline=colour,
                            fill=_lerp(CARD, _lerp(CARD, colour, 0.22), pop))
            c.itemconfigure(self.arc, outline=CARD, extent=0)
            c.itemconfigure(self.check, fill=colour)
            k = _ease((since - 0.05) / 0.28)
            if self.state == "ok":
                pts = [(gx - 3.5, y + 0.5), (gx - 1, y + 3), (gx + 4, y - 3)]
            else:
                pts = [(gx - 3, y - 3), (gx + 3, y + 3)]
            c.coords(self.check, *self._partial(pts, k))
            c.itemconfigure(self.text, fill=_lerp(TEXT, MUTED, since / 0.5))
        c.itemconfigure(self.info, text=self.detail,
                        fill=_lerp(CARD, GOOD if self.state == "ok" else
                                   BAD if self.state == "fail" else GHOST, fade))

    @staticmethod
    def _partial(points, k: float) -> list[float]:
        lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
        left = sum(lengths) * k
        out = [*points[0]]
        for (a, b), length in zip(zip(points, points[1:]), lengths):
            if left >= length:
                out += [*b]
                left -= length
            else:
                f = left / length if length else 0
                out += [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]
                break
        if len(out) == 2:
            out += out
        return out

    def delete(self) -> None:
        for item in self.items:
            self.bar.canvas.delete(item)


class CommandBar:
    def __init__(self) -> None:
        self.target_window = None
        self.events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.pending: list[tuple[str, dict]] = []
        self.next_event_at = 0.0
        self.state = "ready"
        self.state_at = time.time()
        self.running = False
        self.visible = False
        self.chips: list[int] = []
        self.suggestions: list[str] = []
        self.history: list[str] = []
        self.history_index = 0
        self.rows: list[Row] = []
        self.card_h = float(BASE_H)
        self.card_target = float(BASE_H)
        self.progress = 0.0
        self.progress_target = 0.0
        self.progress_colour = ACCENT
        self.level = 0.0
        self.typing = voice.TypingLevel()
        self.mic = None
        self.dictation = None           # (text, envelope, on_done) during --demo
        self.on_result = None           # optional callback(result) for drivers
        self.last_result = None
        self.bound_handle = 0           # the window the last launch bound to
        self.last_trace = None          # agent.trace_record() of the last run

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
        self.home_y = 150
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{self.home_x}+{self.home_y}")

        back = CHROMA if self.transparent else CARD
        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT,
                                highlightthickness=0, bg=back)
        self.canvas.pack(fill="both", expand=True)

        self.card = _round_rect(self.canvas, 1, 1, WIDTH - 1, BASE_H - 1, RADIUS,
                                fill=CARD, outline=EDGE, width=1)
        _round_rect(self.canvas, PAD, FIELD_TOP, WIDTH - PAD, FIELD_TOP + FIELD_H, 13,
                    fill=FIELD, outline=FIELD_EDGE, width=1)

        self.f_mark = tkfont.Font(family="Segoe UI Semibold", size=10)
        self.f_state = tkfont.Font(family="Segoe UI", size=9)
        self.f_meta = tkfont.Font(family="Segoe UI", size=9)
        self.f_chip = tkfont.Font(family="Segoe UI", size=9)
        self.f_row = tkfont.Font(family="Segoe UI", size=10)
        self.f_entry = tkfont.Font(family="Segoe UI", size=16)

        # The orb: a few halo rings faded by hand toward the card colour, a
        # core, and a spinner arc for thinking/acting.
        ox, oy = PAD + 9, HEAD_Y
        self.orb_c = (ox, oy)
        self.halo = [self.canvas.create_oval(ox, oy, ox, oy, outline="", fill=CARD)
                     for _ in range(4)]
        self.ripple = self.canvas.create_oval(ox, oy, ox, oy, outline=CARD, width=2)
        self.core = self.canvas.create_oval(ox - 5, oy - 5, ox + 5, oy + 5, fill=MUTED,
                                            outline="")
        self.spin = self.canvas.create_arc(ox - 10, oy - 10, ox + 10, oy + 10, start=90,
                                           extent=0, style="arc", outline=CARD, width=2)
        self.canvas.create_text(PAD + 26, HEAD_Y, text="jevis", anchor="w",
                                fill=TEXT, font=self.f_mark)
        self.state_text = self.canvas.create_text(
            PAD + 26 + self.f_mark.measure("jevis") + 10, HEAD_Y + 1, text="",
            anchor="w", fill=GHOST, font=self.f_state)
        self.target_text = self.canvas.create_text(
            WIDTH - PAD, HEAD_Y, text="", anchor="e", fill=GHOST, font=self.f_meta)

        # Waveform, inside the right end of the field.
        self.wave = []
        mid = FIELD_TOP + FIELD_H / 2
        for i in range(WAVE_BARS):
            x = WAVE_RIGHT - (WAVE_BARS - i) * (WAVE_W + WAVE_GAP)
            self.wave.append(self.canvas.create_line(x, mid, x, mid + 0.1, fill=FIELD,
                                                     width=WAVE_W, capstyle="round"))

        # The one stock widget, because text input needs a real caret.
        self.entry = tk.Entry(
            self.canvas, font=self.f_entry, bg=FIELD, fg=TEXT, insertbackground=ACCENT,
            relief="flat", borderwidth=0, highlightthickness=0,
            disabledbackground=FIELD, disabledforeground=TEXT,
        )
        wave_left = WAVE_RIGHT - WAVE_BARS * (WAVE_W + WAVE_GAP) - 12
        self.canvas.create_window(PAD + 17, FIELD_TOP + 11, anchor="nw",
                                  window=self.entry, width=wave_left - PAD - 17,
                                  height=FIELD_H - 22)
        self.entry.bind("<Return>", self.submit)
        self.entry.bind("<Escape>", lambda _e: self.hide())
        self.entry.bind("<Tab>", self._accept_suggestion)
        self.entry.bind("<Up>", self._history_back)
        self.entry.bind("<Down>", self._history_forward)
        self.entry.bind("<KeyRelease>", self._on_type)

        self.bar = self.canvas.create_rectangle(
            PAD + 12, FIELD_TOP + FIELD_H - 2, PAD + 12, FIELD_TOP + FIELD_H,
            fill=ACCENT, outline="")

        self.status = self.canvas.create_text(
            PAD + 1, BASE_H - 24, text="", anchor="w", fill=MUTED, font=self.f_meta)
        self.hints = self.canvas.create_text(
            WIDTH - PAD, BASE_H - 24, text="Tab complete    Enter run    Esc close",
            anchor="e", fill=GHOST, font=self.f_meta)

        self.glow = overlay.Glow(self.root)
        self.toast = overlay.Toast(self.root)

        if os.environ.get("JEVIS_MIC") == "1":
            try:
                self.mic = voice.MicLevel()
            except Exception as exc:
                print(f"JEVIS_MIC=1 but no microphone meter ({exc}); using typing cadence.")

        self.root.withdraw()
        self.root.after(FRAME_MS, self._frame)

    # --- painting ----------------------------------------------------------

    def _say(self, text: str, colour: str = MUTED) -> None:
        self.canvas.itemconfigure(self.status, text=text, fill=colour)
        # A result line is long and would collide with the key hints, so the
        # hints yield while one is showing.
        busy = colour in (ACCENT, GOOD, BAD, THINK) or self.state not in ("ready", "listening")
        self.canvas.itemconfigure(self.hints, state="hidden" if busy else "normal")

    def _set_state(self, state: str, label: str | None = None) -> None:
        self.state = state
        self.state_at = time.time()
        colour = {"ready": GHOST, "listening": ACCENT, "thinking": THINK,
                  "acting": ACCENT, "done": GOOD, "failed": BAD}.get(state, GHOST)
        self.canvas.itemconfigure(self.state_text, text=label or state, fill=colour)

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

    def _clear_rows(self) -> None:
        for row in self.rows:
            row.delete()
        self.rows = []

    def _text(self) -> str:
        return self.entry.get().strip()

    # --- the frame loop ----------------------------------------------------

    def _frame(self) -> None:
        now = time.time()
        try:
            self._pump(now)
            if self.visible:
                self._draw(now)
            self.glow.tick()
            self.toast.tick()
        except tk.TclError:
            return
        except Exception as exc:  # never let a paint bug kill the loop
            print(f"frame: {type(exc).__name__}: {exc}")
        self.root.after(FRAME_MS, self._frame)

    def _input_level(self) -> float:
        if self.dictation is not None:
            return self.dictation[1].level()
        if self.mic is not None and self.mic.stream is not None:
            return self.mic.level()
        return self.typing.level()

    def _draw(self, now: float) -> None:
        c = self.canvas
        age = now - self.state_at

        # Card height eases toward its target, so the plan unfolds.
        self.card_h += (self.card_target - self.card_h) * 0.22
        h = self.card_h
        c.coords(self.card, *_rr_points(1, 1, WIDTH - 1, h - 1, RADIUS))
        c.coords(self.status, PAD + 1, h - 24)
        c.coords(self.hints, WIDTH - PAD, h - 24)

        # Input level, smoothed with a fast attack and slow release.
        target = self._input_level() if self.state in ("ready", "listening") else 0.0
        self.level += (target - self.level) * (0.5 if target > self.level else 0.12)
        if self.state == "ready" and self.level > 0.08:
            self._set_state("listening")
        elif self.state == "listening" and self.level < 0.02 and age > 1.2 \
                and self.dictation is None:
            self._set_state("ready")

        self._draw_orb(now, age)
        self._draw_wave(now)

        # Underline: progress while acting, a travelling highlight while thinking.
        self.progress += (self.progress_target - self.progress) * 0.18
        span = WIDTH - PAD * 2 - 24
        x0 = PAD + 12
        if self.state == "thinking":
            head = (now * 0.9) % 1.0
            a, b = max(0.0, head - 0.25), head
            c.coords(self.bar, x0 + span * a, FIELD_TOP + FIELD_H - 2,
                     x0 + span * b, FIELD_TOP + FIELD_H)
            c.itemconfigure(self.bar, fill=THINK, state="normal")
        else:
            c.coords(self.bar, x0, FIELD_TOP + FIELD_H - 2,
                     x0 + span * self.progress, FIELD_TOP + FIELD_H)
            c.itemconfigure(self.bar, fill=self.progress_colour,
                            state="normal" if self.progress > 0.004 else "hidden")

        for row in self.rows:
            row.draw(now)

        # Dictation: the transcript lands as the voice does.
        if self.dictation is not None:
            text, env, on_done = self.dictation
            shown = text[: math.ceil(len(text) * min(1.0, env.elapsed() / env.duration))]
            if self.entry.get() != shown:
                self.entry.delete(0, "end")
                self.entry.insert(0, shown)
                self.entry.icursor("end")
            if env.done():
                self.dictation = None
                self._preview(text)  # the tier, once the whole phrase is in
                self.root.after(520, on_done)

    def _draw_orb(self, now: float, age: float) -> None:
        c = self.canvas
        ox, oy = self.orb_c
        state = self.state
        colour = {"ready": MUTED, "listening": ACCENT, "thinking": THINK,
                  "acting": ACCENT, "done": GOOD, "failed": BAD}[state]
        if state == "ready":
            breathe = 0.5 + 0.5 * math.sin(now * 2.1)
            core_r, glow = 4.5 + breathe * 0.6, 0.18 + 0.12 * breathe
        elif state == "listening":
            core_r, glow = 4.5 + self.level * 2.5, 0.35 + self.level * 0.65
        elif state in ("thinking", "acting"):
            core_r, glow = 4.5 + 0.5 * math.sin(now * 6), 0.55
        else:
            core_r, glow = 5.5, max(0.3, 1 - age / 1.2)

        c.coords(self.core, ox - core_r, oy - core_r, ox + core_r, oy + core_r)
        c.itemconfigure(self.core, fill=colour)
        # Rings drawn outermost first, each a step closer to the core colour.
        for i, ring in enumerate(self.halo):
            k = (len(self.halo) - i) / len(self.halo)
            r = core_r + (2.5 + 9 * glow) * k
            c.coords(ring, ox - r, oy - r, ox + r, oy + r)
            c.itemconfigure(ring, fill=_lerp(CARD, colour, glow * (1 - k) * 0.55 + 0.04))

        if state in ("thinking", "acting"):
            c.itemconfigure(self.spin, outline=colour, start=(90 - now * 360) % 360,
                            extent=100 + 40 * math.sin(now * 3))
        else:
            c.itemconfigure(self.spin, outline=CARD, extent=0)

        if state in ("done", "failed") and age < 0.9:
            t = _ease(age / 0.9)
            r = 6 + 16 * t
            c.coords(self.ripple, ox - r, oy - r, ox + r, oy + r)
            c.itemconfigure(self.ripple, outline=_lerp(colour, CARD, t))
        else:
            c.itemconfigure(self.ripple, outline=CARD)
            c.coords(self.ripple, ox, oy, ox, oy)

    def _draw_wave(self, now: float) -> None:
        mid = FIELD_TOP + FIELD_H / 2
        live = self.state in ("ready", "listening")
        level = self.level if live else 0.0
        base = 1.2 if self.state == "listening" else 0.6
        for i, item in enumerate(self.wave):
            # A travelling shape, weighted toward the middle, scaled by level.
            centre = 1 - abs((i - (WAVE_BARS - 1) / 2) / (WAVE_BARS / 2)) ** 1.6
            wobble = 0.55 + 0.45 * math.sin(now * 9.0 + i * 0.75) * math.sin(now * 3.1 + i * 0.3)
            h = base + level * 15 * (0.25 + 0.75 * centre) * wobble
            x = self.canvas.coords(item)[0]
            self.canvas.coords(item, x, mid - h, x, mid + h)
            tone = (0.18 + 0.82 * min(1.0, h / 12)) if live else 0.0
            self.canvas.itemconfigure(item, fill=_lerp(FIELD, ACCENT, tone))

    # --- worker events -----------------------------------------------------

    def _pump(self, now: float) -> None:
        """Pull worker events onto the Tk thread and replay them at a
        readable pace. Tk is not thread-safe."""
        try:
            while True:
                self.pending.append(self.events.get_nowait())
        except queue.Empty:
            pass
        while self.pending and now >= self.next_event_at:
            kind, data = self.pending.pop(0)
            self._apply(kind, data)
            self.next_event_at = now + DWELL.get(kind, 0.0)

    def _row(self, index: int) -> Row | None:
        return self.rows[index - 1] if 0 < index <= len(self.rows) else None

    def _apply(self, kind: str, data: dict) -> None:
        if kind == "match":
            self._set_state("acting")
            self._clear_rows()
            self.rows = [Row(self, i, label) for i, label in enumerate(data["steps"], 1)]
            self.card_target = ROW_Y + len(self.rows) * ROW_H + 50
            if data["tier"] == "skill":
                self._say(f"deterministic   {data['skill']}   no model", GOOD)
            else:
                self._say(f"model-planned   {data['tier']}", THINK)
            self.progress_target = 0.08
        elif kind == "step":
            row = self._row(data["index"])
            if row:
                row.set("running")
            self.progress_target = (data["index"] - 0.6) / max(1, len(self.rows))
        elif kind == "target":
            row = self._row(data["index"])
            if row and data.get("elements") is not None and not row.detail:
                row.set(row.state, f"{data['elements']} UIA elements")
            if data.get("handle"):
                self.bound_handle = data["handle"]
            if data.get("rect"):
                name = _shorten(data.get("name") or "", 34)
                label = f"{data.get('role') or 'Element'}  ·  {name}" if name else data.get("role", "")
                self.glow.show(data["rect"], ACCENT, label)
        elif kind == "verify":
            row = self._row(data["index"])
            if data["clause"] == "require":
                if row:
                    row.set(row.state, ("require " if data["ok"] else "refused ") + data["detail"])
                return
            if row:
                row.set("ok" if data["ok"] else "fail",
                        ("✓ " if data["ok"] else "✗ ") + data["detail"])
            self.progress_target = data["index"] / max(1, len(self.rows))
        elif kind == "attempt":
            for row in self.rows:
                if row.state == "running":
                    row.set("fail", "retrying")
            self._say(f"attempt {data['attempt']} failed, retrying", BAD)
        elif kind == "result":
            self._finish(data)

    def _finish(self, data: dict) -> None:
        self.running = False
        self.glow.hide()
        for row in self.rows:
            if row.state in ("pending", "running"):
                row.set("ok" if data["ok"] else "fail", row.detail or ("✓" if data["ok"] else ""))
        self.entry.configure(state="normal")
        if data["ok"]:
            self._set_state("done")
            self.progress_target, self.progress_colour = 1.0, GOOD
            self._say(f"done   {data['how']}   {data['elapsed']:.1f}s   verified", GOOD)
            self.toast.show("Done", f"{data['how']}  ·  {data['elapsed']:.1f}s  ·  verified", GOOD)
            if data.get("close"):
                self.root.after(1500, self.hide)
        else:
            self._set_state("failed")
            self.progress_target, self.progress_colour = 1.0, BAD
            self._say(f"failed   {_shorten(data['error'], 88)}", BAD)
            self.toast.show("Didn't do it", _shorten(data["error"], 46), BAD, hold=3.4)
        if self.on_result:
            self.on_result(data)

    # --- input -------------------------------------------------------------

    def _preview(self, instruction: str) -> None:
        """Tell the user which tier this will run on, before they press Enter."""
        instruction = instruction.strip()
        if not instruction:
            self._say(PLACEHOLDER, GHOST)
            self._render_chips(skills.suggest(""))
            return
        if skills.match(instruction):
            self._say("deterministic   no model runs", GOOD)
            self._render_chips([])
        else:
            self._say("model-planned", MUTED)
            self._render_chips(skills.suggest(instruction))

    def _on_type(self, event=None) -> None:
        if self.running:
            return
        if event is not None and event.keysym not in ("Up", "Down", "Tab", "Return", "Escape"):
            self.typing.bump()
        self._preview(self._text())

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

    def dictate(self, text: str, envelope: voice.Envelope, on_done) -> None:
        """Land `text` in the field in step with a voice envelope, then call
        `on_done`. The --demo driver uses this with Windows TTS."""
        self.entry.configure(state="normal")
        self.entry.delete(0, "end")
        self._render_chips([])
        self._set_state("listening")
        self._say("listening", GHOST)
        envelope.start()
        self.dictation = (text, envelope, on_done)

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
        self.pending.clear()
        self.entry.configure(state="normal")
        self.entry.delete(0, "end")
        self.history_index = len(self.history)
        self._clear_rows()
        self.card_target = self.card_h = float(BASE_H)
        self.progress = self.progress_target = 0.0
        self.progress_colour = ACCENT
        self._set_state("ready")
        self._say(PLACEHOLDER, GHOST)
        self._render_chips(skills.suggest(""))
        if self.mic is not None:
            try:
                self.mic.start()
            except Exception as exc:
                print(f"microphone meter unavailable ({exc})")
                self.mic = None

        self.visible = True
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
        total = 9
        if step > total:
            return
        eased = _ease(step / total)
        try:
            self.root.attributes("-alpha", eased)
            self.root.geometry(
                f"{WIDTH}x{HEIGHT}+{self.home_x}+{self.home_y + round(14 * (1 - eased))}")
        except tk.TclError:
            return
        self.root.after(14, lambda: self._enter(step + 1))

    def hide(self) -> None:
        self.running = False
        if self.mic is not None:
            self.mic.stop()
        self._fade_out(0)

    def _fade_out(self, step: int) -> None:
        total = 6
        try:
            if step > total:
                self.root.withdraw()
                self.visible = False
                return
            self.root.attributes("-alpha", 1 - _ease(step / total))
        except tk.TclError:
            return
        self.root.after(14, lambda: self._fade_out(step + 1))

    # --- running -----------------------------------------------------------

    def submit(self, _event=None, close: bool = True) -> None:
        instruction = self._text()
        if not instruction or self.running:
            return
        self.history = [h for h in self.history if h != instruction][-MAX_HISTORY:]
        self.history.append(instruction)
        self.history_index = len(self.history)

        self.entry.configure(state="disabled")
        self._render_chips([])
        self.running = True
        self._set_state("thinking")
        self._say("matching   deterministic skills first", THINK)
        self.next_event_at = time.time() + 0.35   # let "thinking" read before acting
        window = self.target_window
        threading.Thread(target=self._work, args=(instruction, window, close),
                         daemon=True).start()

    def _work(self, instruction: str, window, close: bool) -> None:
        started = time.time()
        log: list[dict] = []

        def emit(kind: str, data: dict) -> None:
            log.append({"t": round(time.time() - started, 3), "kind": kind, **data})
            self.events.put((kind, {k: v for k, v in data.items() if k != "tree"}))

        try:
            result = agent.run(instruction, window=window, verbose=False, on_event=emit)
            self.last_result = result
            self.last_trace = agent.trace_record(instruction, result, log)
            emit("result", {"ok": result.ok, "how": result.skill or result.tier,
                            "elapsed": result.elapsed, "error": result.error or "",
                            "close": close})
        except Exception as exc:
            emit("result", {"ok": False, "how": "", "elapsed": 0.0, "close": False,
                            "error": f"{type(exc).__name__}: {exc}"})

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
    import sys

    _enable_dpi_awareness()
    if "--demo" in sys.argv:
        from . import demo

        return demo.main([a for a in sys.argv[1:] if a != "--demo"])
    CommandBar().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
