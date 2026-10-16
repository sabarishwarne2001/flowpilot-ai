#!/usr/bin/env python3
"""Build Servabo's brand assets: the mark, the wordmark, favicons, app icons and Open Graph cards.

    python -m venv /tmp/brand && /tmp/brand/bin/pip install fonttools brotli cairosvg pillow
    /tmp/brand/bin/python brand/scripts/build_brand_assets.py

The outputs are committed (brand/assets/, frontend/public/, website/public/), so neither the app
nor the website needs these tools to build. Re-run after changing the mark or the colours.

The mark: a document with its top-right corner folded, holding an "S" drawn as one stroke. A
document, kept: what the product does with every promise in a customer's papers. It is built on
a 32-unit grid so it stays legible at 16 px, uses one colour (the brand blue, or currentColor), and
the "S" is cut out of the tile, so the same file works on light and dark backgrounds.

The wordmark is "Servabo" in Inter (SIL Open Font License, bundled with the app as
@fontsource-variable/inter) at weight 640, converted to outlines so it renders identically
everywhere, with no font to load.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "brand"
ASSETS = BRAND / "assets"
TOKENS = json.loads((BRAND / "tokens.json").read_text(encoding="utf-8"))
INTER = ROOT / "frontend/node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2"

BLUE = TOKENS["brandHex"]["primary"]          # the app's --primary (light), as hex
INK = TOKENS["brandHex"]["ink"]               # the app's --foreground (light)
PAPER = TOKENS["brandHex"]["paper"]           # the app's --foreground (dark)
NIGHT = TOKENS["brandHex"]["night"]           # the app's --background (dark)

# ---------------------------------------------------------------------------------------------
# The mark (32 x 32)
# ---------------------------------------------------------------------------------------------

#: The tile: a rounded square whose top-right corner is folded (cut on the diagonal).
TILE = (
    "M8 1H21.2L31 10.8V24a7 7 0 0 1-7 7H8a7 7 0 0 1-7-7V8a7 7 0 0 1 7-7Z"
)
#: The fold itself, a small triangle under the cut, drawn at lower strength.
FOLD = "M21.2 1V7.8a3 3 0 0 0 3 3H31Z"
#: The "S", one stroke with round ends.
S_STROKE = (
    "M21.4 11.6C20.2 10.2 18.3 9.4 16.1 9.4C12.9 9.4 10.7 11 10.7 13.3"
    "C10.7 15.9 13.3 16.6 16.1 17.1C18.9 17.6 21.5 18.4 21.5 21C21.5 23.4 19.2 25 16 25"
    "C13.6 25 11.6 24.1 10.4 22.6"
)
S_WIDTH = 3.3


def s_outline() -> str:
    """The "S" stroke as a filled outline (round ends), so it can be cut out of the tile.

    A mask would do the same in a browser, but mail clients, some favicon renderers and
    rasterisers ignore masks; an even-odd hole works everywhere.
    """
    from shapely.geometry import LineString
    from svgpathtools import parse_path

    path = parse_path(S_STROKE)
    points = [path.point(t / 400) for t in range(401)]
    line = LineString([(p.real, p.imag) for p in points])
    shape = line.buffer(S_WIDTH / 2, quad_segs=16, cap_style="round", join_style="round").simplify(0.01)
    coords = list(shape.exterior.coords)
    return "M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in coords) + "Z"


def mark_svg(*, tile: str, s: str | None, fold_opacity: float = 0.35, title: str = "Servabo") -> str:
    """Tile in `tile` colour, "S" in `s` colour (or cut out of the tile when `s` is None)."""
    if s is None:
        # One colour: the S is a hole in the tile, so it shows whatever is behind it.
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" '
            f'aria-label="{title}"><title>{title}</title>'
            f'<path d="{TILE}{s_outline()}" fill="{tile}" fill-rule="evenodd"/></svg>\n'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" '
        f'aria-label="{title}"><title>{title}</title>'
        f'<path d="{TILE}" fill="{tile}"/>'
        f'<path d="{FOLD}" fill="{s}" fill-opacity="{fold_opacity}"/>'
        f'<path d="{S_STROKE}" fill="none" stroke="{s}" stroke-width="{S_WIDTH}" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>\n'
    )


# ---------------------------------------------------------------------------------------------
# The wordmark
# ---------------------------------------------------------------------------------------------


def wordmark_paths(text: str = "Servabo", weight: float = 640, tracking_em: float = -0.018):
    return text_paths(text, weight=weight, tracking_em=tracking_em)


def text_paths(text: str, *, weight: float, tracking_em: float):
    """(svg path data in font units, advance width, units per em, cap height) for `text`."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    font = TTFont(io.BytesIO(INTER.read_bytes()))
    font = instantiateVariableFont(font, {"wght": weight}, inplace=False)
    upm = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    hmtx = font["hmtx"]
    kern_pairs = {}
    if "GPOS" in font:
        pass  # Inter's kerning for these letters is negligible at this tracking; advances are used.
    x = 0.0
    parts = []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(glyphs)
        # Font units are y-up; SVG is y-down. Flip around the baseline and move along x.
        glyphs[name].draw(TransformPen(pen, (1, 0, 0, -1, x, 0)))
        parts.append(pen.getCommands())
        x += hmtx[name][0] + tracking_em * upm
    width = x - tracking_em * upm
    cap = font["OS/2"].sCapHeight
    del kern_pairs
    return " ".join(parts), width, upm, cap


def logo_svg(*, mark_tile: str, mark_s: str, text: str, title: str = "Servabo") -> str:
    """Mark + wordmark on one line. Height 32 units; the cap height matches the S's height."""
    d, width, upm, cap = wordmark_paths()
    scale = 15.6 / cap                  # cap height 15.6 units, the S is 15.6 units tall
    gap = 9.0
    baseline = 25.0
    total = 32 + gap + width * scale
    mark = mark_svg(tile=mark_tile, s=mark_s).split(f"<title>{title}</title>", 1)[1].rsplit("</svg>", 1)[0]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total:.2f} 32" role="img" '
        f'aria-label="{title}"><title>{title}</title>{mark}'
        f'<path transform="translate({32 + gap:.2f} {baseline}) scale({scale:.6f})" fill="{text}" d="{d}"/>'
        "</svg>\n"
    )


def wordmark_only_svg(*, text: str, title: str = "Servabo") -> str:
    d, width, upm, cap = wordmark_paths()
    scale = 15.6 / cap
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -17 {width * scale:.2f} 22" role="img" '
        f'aria-label="{title}"><title>{title}</title>'
        f'<path transform="scale({scale:.6f})" fill="{text}" d="{d}"/></svg>\n'
    )


# ---------------------------------------------------------------------------------------------
# Raster outputs
# ---------------------------------------------------------------------------------------------


def png(svg: str, size: int, path: Path, *, width: int | None = None, height: int | None = None) -> None:
    import cairosvg

    path.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(path),
                     output_width=width or size, output_height=height or size)


def og_card(path: Path, *, dark: bool, headline: str, sub: str) -> None:
    """A 1200 x 630 Open Graph card: the logo, one headline and one line under it."""
    d, width, upm, cap = wordmark_paths()
    bg, fg, muted = (NIGHT, PAPER, "#a1a1aa") if dark else ("#f4f4f5", INK, "#52525b")
    scale = 44 / cap
    lines = _wrap(headline, 34)
    head = ""
    for i, line in enumerate(lines):
        ld, _, lupm, _ = text_paths(line, weight=700, tracking_em=-0.022)
        head += (f'<path transform="translate(96 {318 + i * 74}) scale({62 / lupm:.6f})" fill="{fg}" '
                 f'd="{ld}"/>')
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">'
        f'<rect width="1200" height="630" fill="{bg}"/>'
        f'<rect x="0" y="0" width="1200" height="8" fill="{BLUE}"/>'
        f'<g transform="translate(96 96) scale(2.6)">'
        f'<path d="{TILE}" fill="{BLUE}"/><path d="{FOLD}" fill="#fff" fill-opacity="0.35"/>'
        f'<path d="{S_STROKE}" fill="none" stroke="#fff" stroke-width="{S_WIDTH}" stroke-linecap="round" '
        'stroke-linejoin="round"/></g>'
        f'<path transform="translate({96 + 32 * 2.6 + 26} {96 + 25 * 2.6}) scale({scale:.6f})" fill="{fg}" d="{d}"/>'
        f"{head}"
        f'<path transform="translate(96 {318 + len(lines) * 74 + 40}) scale({30 / upm:.6f})" fill="{muted}" '
        f'd="{text_paths(sub, weight=450, tracking_em=0)[0]}"/>'
        "</svg>"
    )
    png(svg, 0, path, width=1200, height=630)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line]


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print("wrote", path.relative_to(ROOT))


def main() -> None:
    mark = mark_svg(tile=BLUE, s="#ffffff")
    write(ASSETS / "servabo-mark.svg", mark)
    write(ASSETS / "servabo-mark-mono.svg", mark_svg(tile="currentColor", s=None))
    write(ASSETS / "servabo-logo-light.svg", logo_svg(mark_tile=BLUE, mark_s="#ffffff", text=INK))
    write(ASSETS / "servabo-logo-dark.svg", logo_svg(mark_tile=BLUE, mark_s="#ffffff", text=PAPER))
    write(ASSETS / "servabo-logo-mono.svg", logo_svg(mark_tile="currentColor", mark_s="#ffffff", text="currentColor"))
    write(ASSETS / "servabo-wordmark.svg", wordmark_only_svg(text="currentColor"))
    d, width, upm, cap = wordmark_paths()

    write(BRAND / "mark.ts", (
        "// Generated by brand/scripts/build_brand_assets.py: the Servabo mark and wordmark geometry.\n"
        "// Shared by the app (components/branding/ServaboMark.tsx) and the website. Do not edit.\n\n"
        f"/** The tile: a rounded square with its top-right corner folded (32 x 32 grid). */\n"
        f"export const TILE = {json.dumps(TILE)};\n"
        f"/** The folded corner, drawn at lower strength. */\n"
        f"export const FOLD = {json.dumps(FOLD)};\n"
        f"/** The S as one stroke, drawn {S_WIDTH} units wide with round ends. */\n"
        f"export const S_STROKE = {json.dumps(S_STROKE)};\n"
        f"export const S_WIDTH = {S_WIDTH};\n"
        f"/** The S as a filled outline, for a one-colour mark with the S cut out (fill-rule evenodd). */\n"
        f"export const S_OUTLINE = {json.dumps(s_outline())};\n"
        f"/** \"Servabo\" in Inter 640 as outlines: font units, baseline at y=0, cap height {cap}. */\n"
        f"export const WORDMARK = {{ d: {json.dumps(d)}, width: {width:.1f}, capHeight: {cap} }};\n"
    ))
    for public in (ROOT / "frontend/public", ROOT / "website/public"):
        write(public / "favicon.svg", mark)
        png(mark, 32, public / "favicon-32.png")
        png(mark, 16, public / "favicon-16.png")
        png(mark, 180, public / "apple-touch-icon.png")
        png(mark, 192, public / "icon-192.png")
        png(mark, 512, public / "icon-512.png")

    headline = TOKENS["positioning"]
    for dark in (False, True):
        suffix = "dark" if dark else "light"
        og_card(ROOT / f"website/public/og/servabo-{suffix}.png", dark=dark, headline=headline,
                sub="Online software, available immediately after sign-up")
    og_card(ROOT / "frontend/public/og-servabo.png", dark=False, headline=headline, sub="app.servabo.app")


if __name__ == "__main__":
    main()
