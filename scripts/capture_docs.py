"""Regenerate the screenshots in docs/.

Drives the real command bar through each state and grabs the actual pixels —
no mockups, so the images cannot drift from what the app does.

    ./.venv/Scripts/python.exe scripts/capture_docs.py
"""
from __future__ import annotations

import os
import sys
import time

import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageGrab

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jevis import ui

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
BACKDROP = "#161a22"
MARGIN = 28


def backdrop(bar) -> tk.Toplevel:
    """A plain sheet behind the bar.

    The card keys its background out to get rounded corners, so a raw screen
    grab captures whatever desktop happens to be behind it. This puts a known
    colour there instead.
    """
    sheet = tk.Toplevel(bar.root)
    sheet.overrideredirect(True)
    sheet.configure(bg=BACKDROP)
    sheet.geometry(f"{bar.root.winfo_screenwidth()}x{bar.root.winfo_screenheight()}+0+0")
    sheet.attributes("-topmost", True)
    sheet.lower(bar.root)
    return sheet


def settle(bar, frames: int = 50) -> None:
    for _ in range(frames):
        bar.root.update()
        time.sleep(0.02)


def shot(bar) -> Image.Image:
    settle(bar)
    x, y = bar.root.winfo_rootx(), bar.root.winfo_rooty()
    # The window is as tall as the longest plan; only the card is drawn.
    return ImageGrab.grab((x, y, x + ui.WIDTH, y + round(bar.card_target)))


def framed(image: Image.Image) -> Image.Image:
    """Put the card on a backdrop so its rounded corners read properly."""
    canvas = Image.new("RGB", (image.width + MARGIN * 2, image.height + MARGIN * 2), BACKDROP)
    canvas.paste(image, (MARGIN, MARGIN))
    return canvas


def stack(images: list[Image.Image], gap: int = 14) -> Image.Image:
    width = max(i.width for i in images)
    height = sum(i.height for i in images) + gap * (len(images) - 1)
    canvas = Image.new("RGB", (width + MARGIN * 2, height + MARGIN * 2), BACKDROP)
    y = MARGIN
    for image in images:
        canvas.paste(image, (MARGIN, y))
        y += image.height + gap
    return canvas


def _label_font() -> ImageFont.ImageFont:
    for path in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, 14)
    return ImageFont.load_default()


def label(image: Image.Image, text: str) -> Image.Image:
    strip = Image.new("RGB", (image.width, 22), BACKDROP)
    ImageDraw.Draw(strip).text((2, 3), text, fill="#7b8599", font=_label_font())
    canvas = Image.new("RGB", (image.width, image.height + 22), BACKDROP)
    canvas.paste(strip, (0, 0))
    canvas.paste(image, (0, 22))
    return canvas


def main() -> int:
    ui._enable_dpi_awareness()
    os.makedirs(DOCS, exist_ok=True)

    bar = ui.CommandBar()
    bar.show()
    sheet = backdrop(bar)
    bar.root.lift()
    settle(bar, 40)

    def target(text: str = "Untitled - Notepad") -> None:
        # show() captures whatever window was focused. These images are
        # public, so pin a neutral target instead of shipping what was on screen.
        bar.canvas.itemconfigure(bar.target_text, text=text)

    def typed(text: str) -> None:
        bar.entry.configure(state="normal")
        bar.entry.delete(0, "end")
        bar.entry.insert(0, text)
        bar._preview(text)

    target()
    idle = shot(bar)

    # Listening: hold the input level up the way a voice would.
    bar._input_level = lambda: 0.55 + 0.35 * abs(__import__("math").sin(time.time() * 7))
    typed("open notepad and write hel")
    bar._set_state("listening")
    bar._say("listening", ui.GHOST)
    listening = shot(bar)
    bar._input_level = lambda: 0.0
    bar.level = 0.0

    typed("open my default browser")
    bar._set_state("ready")
    deterministic = shot(bar)

    typed("open notepad and write a poem about rain")
    planned = shot(bar)

    command = "open notepad and write hello world"
    typed(command)
    bar.entry.configure(state="disabled")
    bar._render_chips([])
    bar.running = True
    bar._set_state("thinking")
    bar._say("matching   deterministic skills first", ui.THINK)
    thinking = shot(bar)

    bar._apply("match", {"tier": "skill", "skill": "open_and_write",
                         "steps": ["launch notepad", "type 'hello world'"]})
    bar._apply("step", {"index": 1})
    bar._apply("target", {"index": 1, "elements": 41})
    bar._apply("verify", {"index": 1, "ok": True, "clause": "expect", "detail": "app"})
    bar._apply("step", {"index": 2})
    bar._apply("verify", {"index": 2, "ok": True, "clause": "require", "detail": "editor_empty"})
    acting = shot(bar)

    bar._apply("verify", {"index": 2, "ok": True, "clause": "expect", "detail": "editor_contains"})
    bar._finish({"ok": True, "how": "open_and_write", "elapsed": 2.6, "error": "", "close": False})
    done = shot(bar)
    framed(done).save(os.path.join(DOCS, "hero.png"))

    bar._clear_rows()
    bar.card_target = bar.card_h = float(ui.BASE_H)
    bar.progress = bar.progress_target = 0.0
    typed("open spotify")
    bar._finish({"ok": False, "how": "", "elapsed": 0.0, "close": False,
                 "error": "no supported app. jevis can open: browser, calc, explorer, "
                          "mspaint, notepad, terminal"})
    failed = shot(bar)

    stack([
        label(idle, "ready  ·  suggestions offered, orb breathing"),
        label(listening, "listening  ·  waveform follows the input level"),
        label(deterministic, "deterministic  ·  no model will run"),
        label(planned, "model-planned  ·  a model will compose the text"),
        label(thinking, "thinking  ·  choosing a tier"),
        label(acting, "acting  ·  each step ticks off as its postcondition is read back"),
        label(done, "done  ·  verified, a toast confirms it"),
        label(failed, "refused  ·  names what it can open, never substitutes"),
    ]).save(os.path.join(DOCS, "states.png"))

    sheet.destroy()
    bar.root.destroy()
    print(f"wrote hero.png and states.png in {DOCS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
