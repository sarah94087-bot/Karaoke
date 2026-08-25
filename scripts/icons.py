"""The app's icons, drawn rather than committed as blobs nobody can redraw.

T-5.3 needs a 192px and a 512px PNG or no browser will offer to install the
app, plus a maskable one so Android does not paste a white square behind it.

Written by hand for the reason the SigV4 signing, the SSE parser and the Sentry
envelope are (T-3.1, D-18's repair, T-3.12): the alternative was Pillow - tens
of megabytes and a build dependency - to draw a rounded rectangle and a circle.
A PNG is a zlib stream with a four-byte header per chunk, and that is the whole
format as far as this file needs it.

Run it after changing the palette in `globals.css`:

    .venv\\Scripts\\python.exe scripts\\icons.py

It is not run at build time. The icons change about once a year and a
generated asset that is regenerated on every deploy is a diff nobody reads.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "apps" / "web" / "public"

# The same three values as `globals.css`. Written out rather than parsed: two
# files that must agree is worth a comment, not a CSS parser.
BACKGROUND = (0x10, 0x10, 0x1A)
ACCENT = (0x7C, 0x5C, 0xFF)
TEXT = (0xF2, 0xF2, 0xF7)

# Every shape is drawn at this multiple and then averaged down, which is the
# cheapest antialiasing there is and the only one this file needs.
SUPERSAMPLE = 4


class Canvas:
    """A plain RGB raster. Coordinates are floats in units of the final pixel."""

    def __init__(self, size: int, fill: tuple[int, int, int]):
        self.size = size * SUPERSAMPLE
        self.scale = SUPERSAMPLE
        self.pixels = bytearray(bytes(fill) * (self.size * self.size))

    def _set(self, x: int, y: int, colour: tuple[int, int, int]) -> None:
        at = (y * self.size + x) * 3
        self.pixels[at : at + 3] = bytes(colour)

    def disc(self, cx: float, cy: float, radius: float, colour: tuple[int, int, int]) -> None:
        cx, cy, radius = cx * self.scale, cy * self.scale, radius * self.scale
        top, bottom = max(0, int(cy - radius)), min(self.size, int(cy + radius) + 1)
        for y in range(top, bottom):
            for x in range(max(0, int(cx - radius)), min(self.size, int(cx + radius) + 1)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                    self._set(x, y, colour)

    def rounded(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        radius: float,
        colour: tuple[int, int, int],
    ) -> None:
        s = self.scale
        x0, y0, x1, y1, radius = x0 * s, y0 * s, x1 * s, y1 * s, radius * s
        # A radius over half the shorter side inverts the clamp below and draws
        # a half-disc instead of a capsule - which is exactly what the first
        # run of this file produced.
        radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
        for y in range(max(0, int(y0)), min(self.size, int(y1) + 1)):
            for x in range(max(0, int(x0)), min(self.size, int(x1) + 1)):
                # Only the four corners need the distance test; everything
                # between the insets is inside by definition.
                near_x = min(max(x, x0 + radius), x1 - radius)
                near_y = min(max(y, y0 + radius), y1 - radius)
                if (x - near_x) ** 2 + (y - near_y) ** 2 <= radius * radius:
                    self._set(x, y, colour)

    def ring(
        self, cx: float, cy: float, radius: float, width: float, colour: tuple[int, int, int]
    ) -> None:
        s = self.scale
        cx, cy, radius, width = cx * s, cy * s, radius * s, width * s
        inner = (radius - width) ** 2
        for y in range(max(0, int(cy - radius)), min(self.size, int(cy + radius) + 1)):
            for x in range(max(0, int(cx - radius)), min(self.size, int(cx + radius) + 1)):
                d = (x - cx) ** 2 + (y - cy) ** 2
                # The open bottom of a microphone's cradle: the top half is
                # the bracket, the bottom is where the stand meets it.
                if inner <= d <= radius * radius and y >= cy - width:
                    self._set(x, y, colour)

    def downsample(self) -> tuple[int, bytes]:
        out_size = self.size // self.scale
        out = bytearray(out_size * out_size * 3)
        block = self.scale * self.scale
        for y in range(out_size):
            for x in range(out_size):
                totals = [0, 0, 0]
                for dy in range(self.scale):
                    row = (y * self.scale + dy) * self.size
                    for dx in range(self.scale):
                        at = (row + x * self.scale + dx) * 3
                        totals[0] += self.pixels[at]
                        totals[1] += self.pixels[at + 1]
                        totals[2] += self.pixels[at + 2]
                at = (y * out_size + x) * 3
                out[at] = totals[0] // block
                out[at + 1] = totals[1] // block
                out[at + 2] = totals[2] // block
        return out_size, bytes(out)


def write_png(path: Path, size: int, rgb: bytes) -> None:
    """The minimum viable PNG: 8-bit RGB, one filter byte per row, no frills."""
    raw = b"".join(b"\x00" + rgb[y * size * 3 : (y + 1) * size * 3] for y in range(size))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def draw(size: int, *, maskable: bool) -> Canvas:
    """A microphone in a cradle, which is what this app is.

    `maskable` keeps everything inside the middle 80%, because Android crops a
    maskable icon to whatever shape the launcher uses and anything in the outer
    ring is not guaranteed to survive.
    """
    canvas = Canvas(size, BACKGROUND)
    unit = size / 100
    inset = 0.8 if maskable else 1.0

    def u(value: float) -> float:
        """A hundredth of the icon, shrunk into the safe zone when masked."""
        return (50 + (value - 50) * inset) * unit

    # The capsule: a radius of half its width is what makes the ends round.
    canvas.rounded(u(38), u(20), u(62), u(58), u(50) - u(38), ACCENT)
    # The cradle around it, and the stand down to the base.
    canvas.ring(u(50), u(50), u(76) - u(50), u(56) - u(50), TEXT)
    canvas.rounded(u(47), u(70), u(53), u(84), u(50) - u(47), TEXT)
    canvas.rounded(u(34), u(80), u(66), u(86), u(50) - u(47), TEXT)
    return canvas


def main() -> int:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    wanted = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
        # iOS ignores the manifest's icons and reads this one from a <link>.
        ("apple-touch-icon.png", 180, True),
    ]
    for name, size, maskable in wanted:
        out_size, rgb = draw(size, maskable=maskable).downsample()
        write_png(PUBLIC / name, out_size, rgb)
        print(f"{name:26} {size}x{size}  {(PUBLIC / name).stat().st_size / 1024:.1f}KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
