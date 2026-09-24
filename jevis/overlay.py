"""Per-pixel-alpha overlays: the target glow and the completion toast.

Tk has no alpha channel, so these are not Tk-drawn. Each overlay is a Tk
Toplevel turned into a Win32 layered window; the pixels are rendered once with
Pillow and handed to `UpdateLayeredWindow`. After that the compositor does the
work: fading and pulsing only change the window's constant alpha, and sliding
only changes its position, so a frame costs one API call rather than a render.

Both overlays are click-through (`WS_EX_TRANSPARENT`) and never take focus
(`WS_EX_NOACTIVATE`), so they cannot steal input from the app being driven or
from the user.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import os
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 0x2
AC_SRC_ALPHA = 0x1

_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.GetParent.restype = wt.HWND
_user32.GetParent.argtypes = [wt.HWND]
_user32.GetDC.restype = wt.HDC
_user32.GetDC.argtypes = [wt.HWND]
_user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
_gdi32.CreateCompatibleDC.restype = wt.HDC
_gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
_gdi32.CreateDIBSection.restype = wt.HBITMAP
_gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
_gdi32.SelectObject.restype = wt.HGDIOBJ
_gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
_gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wt.HDC]


class _BLEND(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_byte)]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


_user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
    ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(_BLEND), wt.DWORD]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = ("seguisb.ttf", "segoeui.ttf") if bold else ("segoeui.ttf",)
    for name in names:
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


class Layer:
    """One layered, click-through, never-focused window showing an RGBA image."""

    def __init__(self, root: tk.Misc) -> None:
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.geometry("1x1+-32000+-32000")
        self.top.attributes("-topmost", True)
        self.top.update_idletasks()
        self.hwnd = _user32.GetParent(self.top.winfo_id()) or self.top.winfo_id()
        style = _user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        _user32.SetWindowLongPtrW(
            self.hwnd, GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
            | WS_EX_TOPMOST | WS_EX_NOACTIVATE)
        self.memdc = None
        self.bitmap = None
        self.old = None
        self.size = (0, 0)
        self.pos = (0, 0)
        self.alpha = 0

    def paint(self, image: Image.Image, x: int, y: int, alpha: int = 0) -> None:
        self._release()
        image = image.convert("RGBA")
        w, h = image.size
        premul = _premultiply(image)  # ULW_ALPHA wants premultiplied BGRA
        header = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        screen = _user32.GetDC(None)
        self.memdc = _gdi32.CreateCompatibleDC(screen)
        self.bitmap = _gdi32.CreateDIBSection(screen, ctypes.byref(header), 0,
                                              ctypes.byref(bits), None, 0)
        _user32.ReleaseDC(None, screen)
        raw = premul.tobytes()
        ctypes.memmove(bits, raw, len(raw))
        self.old = _gdi32.SelectObject(self.memdc, self.bitmap)
        self.size = (w, h)
        self.top.deiconify()
        self.set(x, y, alpha)

    def set(self, x: int | None = None, y: int | None = None, alpha: int | None = None) -> None:
        if self.memdc is None:
            return
        if x is not None and y is not None:
            self.pos = (int(x), int(y))
        if alpha is not None:
            self.alpha = max(0, min(255, int(alpha)))
        blend = _BLEND(0, 0, self.alpha, AC_SRC_ALPHA)
        screen = _user32.GetDC(None)
        _user32.UpdateLayeredWindow(
            self.hwnd, screen, ctypes.byref(wt.POINT(*self.pos)), ctypes.byref(wt.SIZE(*self.size)),
            self.memdc, ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
        _user32.ReleaseDC(None, screen)

    def _release(self) -> None:
        if self.memdc is not None:
            _gdi32.SelectObject(self.memdc, self.old)
            _gdi32.DeleteObject(self.bitmap)
            _gdi32.DeleteDC(self.memdc)
            self.memdc = self.bitmap = self.old = None

    def destroy(self) -> None:
        self._release()
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def _premultiply(image: Image.Image) -> Image.Image:
    """RGBA -> premultiplied, channel order swapped to BGRA for the DIB."""
    from PIL import ImageChops

    r, g, b, a = image.split()
    return Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a),
                                ImageChops.multiply(r, a), a))


# --- the glow around the element being acted on ---------------------------

GLOW_MARGIN = 28
LABEL_H = 26


def render_glow(width: int, height: int, colour: str, label: str = "") -> tuple[Image.Image, int, int]:
    """Glow outline for a (width x height) rect. Returns image and its offset."""
    m = GLOW_MARGIN
    top_extra = LABEL_H + 6 if label else 0
    W, H = width + 2 * m, height + 2 * m + top_extra
    rgb = _rgb(colour)
    ox, oy = m, m + top_extra
    radius = 8

    # Blur the alpha alone over a solid colour. Blurring RGBA directly mixes
    # in the transparent pixels' black and the glow comes out grey.
    halo = Image.new("L", (W, H), 0)
    ImageDraw.Draw(halo).rounded_rectangle(
        (ox - 3, oy - 3, ox + width + 3, oy + height + 3), radius + 3, outline=255, width=7)
    halo = halo.filter(ImageFilter.GaussianBlur(9)).point(lambda v: min(255, v * 2))
    # Outer glow only: light spilling inward reads as haze over a light app.
    from PIL import ImageChops

    keep = Image.new("L", (W, H), 255)
    ImageDraw.Draw(keep).rounded_rectangle((ox + 1, oy + 1, ox + width - 1, oy + height - 1),
                                           radius, fill=0)
    glow = Image.new("RGBA", (W, H), rgb + (0,))
    glow.putalpha(ImageChops.multiply(halo, keep))

    crisp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(crisp)
    draw.rounded_rectangle((ox, oy, ox + width, oy + height), radius,
                           fill=rgb + (12,), outline=rgb + (255,), width=2)
    # Corner ticks read as "targeted" even on a busy background.
    tick = min(18, width // 4, height // 4)
    light = tuple(min(255, c + 70) for c in rgb) + (255,)
    for cx, cy, dx, dy in ((ox, oy, 1, 1), (ox + width, oy, -1, 1),
                           (ox, oy + height, 1, -1), (ox + width, oy + height, -1, -1)):
        draw.line((cx, cy + dy * tick, cx, cy, cx + dx * tick, cy), fill=rgb + (255,), width=3)

    image = Image.alpha_composite(glow, crisp)
    if label:
        font = _font(13, bold=True)
        text_w = int(draw.textlength(label, font=font))
        pill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pill)
        x0, y0 = ox, m - 2
        pd.rounded_rectangle((x0, y0, x0 + text_w + 22, y0 + LABEL_H), LABEL_H // 2,
                             fill=(11, 14, 21, 235), outline=rgb + (255,), width=1)
        pd.ellipse((x0 + 9, y0 + 10, x0 + 15, y0 + 16), fill=rgb + (255,))
        pd.text((x0 + 20, y0 + LABEL_H // 2), label, font=font, fill=light, anchor="lm")
        image = Image.alpha_composite(image, pill)
    return image, -m, -(m + top_extra)


class Glow:
    """A pulsing outline around a screen rect, from its UIA BoundingRectangle."""

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.layer: Layer | None = None
        self.shown_at = 0.0
        self.fading = False
        self.fade_from = 0.0

    def show(self, rect, colour: str, label: str = "") -> None:
        left, top, right, bottom = rect
        if right - left < 4 or bottom - top < 4:
            return
        if self.layer is None:
            self.layer = Layer(self.root)
        image, dx, dy = render_glow(right - left, bottom - top, colour, label)
        self.layer.paint(image, left + dx, top + dy, 0)
        self.shown_at = time.time()
        self.fading = False

    def hide(self) -> None:
        if self.layer is not None and not self.fading:
            self.fading = True
            self.fade_from = time.time()

    def tick(self) -> None:
        """Called every frame by the bar's animation loop."""
        if self.layer is None or self.layer.memdc is None:
            return
        now = time.time()
        if self.fading:
            t = (now - self.fade_from) / 0.28
            if t >= 1:
                self.layer.destroy()
                self.layer = None
                return
            self.layer.set(alpha=self.layer.alpha * (1 - t) if t < 0.99 else 0)
            return
        t = now - self.shown_at
        intro = min(1.0, t / 0.22)
        eased = 1 - (1 - intro) ** 3
        pulse = 0.82 + 0.18 * math.cos(t * 5.2)
        self.layer.set(alpha=255 * eased * (pulse if intro >= 1 else 1))


# --- the completion toast ---------------------------------------------------

TOAST_W, TOAST_H = 360, 78
SHADOW = 22


def render_toast(title: str, detail: str, colour: str, progress: float) -> Image.Image:
    """Card with a soft shadow; `progress` draws the check (or cross) stroke."""
    W, H = TOAST_W + SHADOW * 2, TOAST_H + SHADOW * 2
    rgb = _rgb(colour)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (SHADOW, SHADOW + 6, SHADOW + TOAST_W, SHADOW + TOAST_H + 6), 16, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    x0, y0 = SHADOW, SHADOW
    d.rounded_rectangle((x0, y0, x0 + TOAST_W, y0 + TOAST_H), 16,
                        fill=(11, 14, 21, 250), outline=(38, 46, 62, 255), width=1)
    cx, cy, r = x0 + 40, y0 + TOAST_H // 2, 17
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tuple(int(c * 0.22) for c in rgb) + (255,),
              outline=rgb + (255,), width=2)
    if colour.lower() in ("#34d399",):
        points = [(cx - 8, cy + 1), (cx - 2, cy + 7), (cx + 9, cy - 6)]
    else:
        points = [(cx - 6, cy - 6), (cx + 6, cy + 6)]
    _stroke(d, points, progress, rgb + (255,), 3)
    if len(points) == 2:
        _stroke(d, [(cx + 6, cy - 6), (cx - 6, cy + 6)], max(0.0, progress * 2 - 1), rgb + (255,), 3)

    d.text((x0 + 70, y0 + 27), title, font=_font(15, bold=True), fill=(242, 244, 249, 255),
           anchor="lm")
    d.text((x0 + 70, y0 + 51), detail, font=_font(12), fill=(123, 133, 153, 255), anchor="lm")
    return Image.alpha_composite(shadow, card)


def _stroke(draw: ImageDraw.ImageDraw, points, progress: float, fill, width: int) -> None:
    """Draw the first `progress` fraction of a polyline."""
    if progress <= 0:
        return
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    remaining = sum(lengths) * min(1.0, progress)
    path = [points[0]]
    for (a, b), length in zip(zip(points, points[1:]), lengths):
        if remaining >= length:
            path.append(b)
            remaining -= length
        else:
            f = remaining / length if length else 0
            path.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            break
    draw.line(path, fill=fill, width=width, joint="curve")


class Toast:
    """Slides in bottom-right of the work area, draws its check, then leaves."""

    FRAMES = 12

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.layer: Layer | None = None
        self.frames: list[Image.Image] = []
        self.started = 0.0
        self.hold = 2.6
        self.area = None   # (left, top, right, bottom) to anchor in; default: work area

    def show(self, title: str, detail: str, colour: str, hold: float = 2.6) -> None:
        if self.layer is None:
            self.layer = Layer(self.root)
        self.frames = [render_toast(title, detail, colour, i / (self.FRAMES - 1))
                       for i in range(self.FRAMES)]
        self.frame = -1
        self.hold = hold
        self.started = time.time()
        if self.area is None:
            work = wt.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)
            right, bottom = work.right, work.bottom
        else:
            right, bottom = self.area[2], self.area[3]
        self.anchor = (right - TOAST_W - SHADOW - 18, bottom - TOAST_H - SHADOW - 18)
        self._paint(0, 0, 0)

    def _paint(self, index: int, dx: float, alpha: float) -> None:
        if index != self.frame:
            self.layer.paint(self.frames[index], self.anchor[0] + dx, self.anchor[1], int(alpha))
            self.frame = index
        else:
            self.layer.set(self.anchor[0] + dx, self.anchor[1], int(alpha))

    def active(self) -> bool:
        return self.layer is not None and bool(self.frames)

    def tick(self) -> None:
        if not self.active():
            return
        t = time.time() - self.started
        enter, leave = 0.34, 0.3
        if t < enter:
            e = 1 - (1 - t / enter) ** 3
            self._paint(0, 40 * (1 - e), 255 * e)
        elif t < enter + self.hold:
            k = min(1.0, (t - enter) / 0.4)
            self._paint(round(k * (self.FRAMES - 1)), 0, 255)
        elif t < enter + self.hold + leave:
            e = (t - enter - self.hold) / leave
            self._paint(self.FRAMES - 1, 24 * e * e, 255 * (1 - e))
        else:
            self.layer.destroy()
            self.layer = None
            self.frames = []
