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


def settle(bar, frames: int = 22) -> None:
    for _ in range(frames):
        bar.root.update()
        time.sleep(0.02)


def shot(bar) -> Image.Image:
    settle(bar)
    x, y = bar.root.winfo_rootx(), bar.root.winfo_rooty()
    return ImageGrab.grab((x, y, x + ui.WIDTH, y + ui.HEIGHT))


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
    # show() captures whatever window was focused. These images are public, so
    # pin a neutral target instead of shipping whatever was on screen.
    bar.canvas.itemconfigure(bar.target_text, text="Untitled - Notepad")
    settle(bar, 40)

    idle = shot(bar)
    framed(idle).save(os.path.join(DOCS, "hero.png"))

    bar.entry.insert(0, "open my default browser")
    bar._on_type()
    bar.canvas.itemconfigure(bar.target_text, text="Untitled - Notepad")
    deterministic = shot(bar)

    bar.entry.delete(0, "end")
    bar.entry.insert(0, "open notepad and write a poem about rain")
    bar._on_type()
    bar.canvas.itemconfigure(bar.target_text, text="Untitled - Notepad")
    planned = shot(bar)

    bar.entry.delete(0, "end")
    bar.entry.insert(0, "open notepad and write hello world")
    bar._on_type()
    bar.running = True
    bar.phase = 0.42
    bar._animate()
    bar._say("running   open notepad and write hello world", ui.ACCENT)
    bar.canvas.itemconfigure(bar.target_text, text="Untitled - Notepad")
    running = shot(bar)

    bar.running = False
    bar._dot(ui.GOOD)
    bar._underline(1.0, ui.GOOD)
    bar._say("done   open_and_write   3.4s", ui.GOOD)
    bar.canvas.itemconfigure(bar.target_text, text="Untitled - Notepad")
    done = shot(bar)

    bar.entry.delete(0, "end")
    bar.entry.insert(0, "open spotify")
    bar._on_type()
    bar._dot(ui.BAD)
    bar._underline(1.0, ui.BAD)
    bar._say("failed   no supported app. jevis can open: browser, calc, explorer, "
             "mspaint, notepad, terminal", ui.BAD)
    bar.canvas.itemconfigure(bar.target_text, text="Untitled - Notepad")
    failed = shot(bar)

    stack([
        label(idle, "idle  ·  suggestions offered"),
        label(deterministic, "deterministic  ·  no model will run"),
        label(planned, "model-planned  ·  a model will compose the text"),
        label(running, "running"),
        label(done, "done"),
        label(failed, "refused  ·  names what it can open, never substitutes"),
    ]).save(os.path.join(DOCS, "states.png"))

    sheet.destroy()
    bar.root.destroy()
    print(f"wrote {DOCS}\\hero.png and states.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
