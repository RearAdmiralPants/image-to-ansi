"""img2ansi.py  --  convert an image to a truecolor ANSI PowerShell script.

Each terminal cell is fit to a (character, fg, bg) triple by matching the
character's four quadrant ink-coverages against the 2x2 block of colors
sampled from the image. For each candidate character the optimal fg/bg is
found by 2x2 least-squares per color channel; the character with the
lowest residual wins.

Usage (typical):
    python img2ansi.py input.png --cols 71
    python img2ansi.py input.png out.ps1 --cols 100 --ramp blocks
    python img2ansi.py input.png --cols 120 --ramp full --font C:\\Windows\\Fonts\\consola.ttf

Dependencies: Pillow, numpy. --ramp full additionally requires fonttools.
"""

import argparse
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont


# --- Ramp presets -----------------------------------------------------------

# The 16 Unicode quadrant-block characters cover every binary 2x2 coverage
# pattern exactly. Ordered (tl, tr, bl, br) bits low-to-high in the comment.
BLOCK_CHARS: List[str] = [
    " ",        # 0 0 0 0
    "\u2598",   # 1 0 0 0   upper-left quadrant
    "\u259d",   # 0 1 0 0   upper-right quadrant
    "\u2580",   # 1 1 0 0   upper half
    "\u2596",   # 0 0 1 0   lower-left quadrant
    "\u258c",   # 1 0 1 0   left half
    "\u259e",   # 0 1 1 0   diagonal TR-BL
    "\u259b",   # 1 1 1 0   upper-left triple
    "\u2597",   # 0 0 0 1   lower-right quadrant
    "\u259a",   # 1 0 0 1   diagonal TL-BR
    "\u2590",   # 0 1 0 1   right half
    "\u259c",   # 1 1 0 1   upper-right triple
    "\u2584",   # 0 0 1 1   lower half
    "\u2599",   # 1 0 1 1   lower-left triple
    "\u259f",   # 0 1 1 1   lower-right triple
    "\u2588",   # 1 1 1 1   full block
]

# Curated ASCII with non-binary coverages (adds texture between block extremes).
CURATED_ASCII: List[str] = list(" .,:;!|/\\()[]{}<>?*+=-_`'\"^~oO0#@&$%")

# Theoretical quadrant coverages for characters with a known, exact ink
# layout. Measuring these via PIL rasterization is unreliable because:
#   (a) several quadrant blocks may simply not exist in a given font -- PIL
#       silently falls back to .notdef (tofu) and the measured coverage is
#       whatever shape the tofu box happens to be;
#   (b) even when present, block glyphs don't align perfectly with the
#       cell_w/2 x cell_h/2 split we use for sampling (the font's em-square
#       midline almost never equals (ascent+descent)/2), so measurements
#       come out like ~0.85 / ~0.15 instead of 1.0 / 0.0. The LSQ fit then
#       pushes fg/bg past the true target colors to compensate, and the
#       terminal renders the actual block at 100% coverage -- net result is
#       oversaturated output with haloing around edges.
# Using theoretical coverages here matches what the terminal will actually
# draw: block glyphs are designed to fill exact halves / quarters of their
# em cell.
KNOWN_COVERAGES: Dict[str, Tuple[float, float, float, float]] = {
    " ":      (0.0, 0.0, 0.0, 0.0),
    "\u2588": (1.0, 1.0, 1.0, 1.0),   # full block
    "\u2580": (1.0, 1.0, 0.0, 0.0),   # upper half
    "\u2584": (0.0, 0.0, 1.0, 1.0),   # lower half
    "\u258c": (1.0, 0.0, 1.0, 0.0),   # left half
    "\u2590": (0.0, 1.0, 0.0, 1.0),   # right half
    "\u2598": (1.0, 0.0, 0.0, 0.0),   # upper-left quadrant
    "\u259d": (0.0, 1.0, 0.0, 0.0),   # upper-right quadrant
    "\u2596": (0.0, 0.0, 1.0, 0.0),   # lower-left quadrant
    "\u2597": (0.0, 0.0, 0.0, 1.0),   # lower-right quadrant
    "\u259a": (1.0, 0.0, 0.0, 1.0),   # diagonal UL+LR
    "\u259e": (0.0, 1.0, 1.0, 0.0),   # diagonal UR+LL
    "\u2599": (1.0, 0.0, 1.0, 1.0),   # UL + lower half
    "\u259b": (1.0, 1.0, 1.0, 0.0),   # upper half + LL
    "\u259c": (1.0, 1.0, 0.0, 1.0),   # upper half + LR
    "\u259f": (0.0, 1.0, 1.0, 1.0),   # UR + lower half
    # Uniform partial-coverage shade characters:
    "\u2591": (0.25, 0.25, 0.25, 0.25),   # light shade
    "\u2592": (0.50, 0.50, 0.50, 0.50),   # medium shade
    "\u2593": (0.75, 0.75, 0.75, 0.75),   # dark shade
}

PLACEHOLDER = "\uE000"  # PUA codepoint; substituted for ESC at runtime.


# --- Font / glyph rasterization --------------------------------------------

def find_default_font() -> str:
    candidates = [
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\lucon.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    raise FileNotFoundError(
        "No default monospace TTF found; pass --font PATH explicitly."
    )


def measure_cell(font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    """Return (cell_width, cell_height) in pixels, rounded to be even."""
    ascent, descent = font.getmetrics()
    cell_h = ascent + descent
    # Use 'M' as a representative full-width monospace glyph.
    left, _, right, _ = font.getbbox("M")
    cell_w = right - left
    # Force even dimensions so each quadrant is a clean integer block.
    cell_w = max(2, (cell_w // 2) * 2)
    cell_h = max(2, (cell_h // 2) * 2)
    return cell_w, cell_h


def rasterize_quadrants(
    ch: str,
    font: ImageFont.FreeTypeFont,
    cell_w: int,
    cell_h: int,
) -> Optional[np.ndarray]:
    """Render `ch` into a cell_w x cell_h grayscale bitmap and return the
    mean ink coverage of each quadrant as a float32 array [tl, tr, bl, br]
    in [0, 1]. Returns None if the glyph renders entirely empty (except for
    a literal space, which legitimately has zero coverage)."""
    img = Image.new("L", (cell_w, cell_h), 0)
    draw = ImageDraw.Draw(img)
    # Position at (0, 0) of the cell. Some glyphs will have bearings that
    # clip, but that's acceptable -- the quadrant ratios are what we want.
    draw.text((0, 0), ch, font=font, fill=255)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    hh, hw = cell_h // 2, cell_w // 2
    cov = np.array([
        arr[:hh, :hw].mean(),
        arr[:hh, hw:].mean(),
        arr[hh:, :hw].mean(),
        arr[hh:, hw:].mean(),
    ], dtype=np.float32)
    if ch != " " and cov.sum() < 1e-4:
        return None  # glyph is effectively blank -- skip.
    return cov


def _is_keepable_codepoint(cp: int) -> bool:
    """Filter out codepoints unsuitable for monospace cell rendering."""
    if cp < 0x20:
        return False                    # C0 controls
    if 0x7F <= cp <= 0xA0:
        return False                    # DEL + C1 controls + NBSP
    if 0xD800 <= cp <= 0xDFFF:
        return False                    # surrogates
    if 0xE000 <= cp <= 0xF8FF:
        return False                    # BMP PUA (collides with placeholder)
    if 0xFFF0 <= cp <= 0xFFFF:
        return False                    # specials
    cat = unicodedata.category(chr(cp))
    if cat.startswith("M"):             # combining marks
        return False
    if cat in ("Cf", "Cn", "Co", "Cs"):  # format, unassigned, private, surrogate
        return False
    if cat in ("Zl", "Zp"):             # line/paragraph separators
        return False
    return True


def _read_font_cmap(font_path: str) -> Optional[Set[int]]:
    """Return the set of codepoints the font actually maps to glyphs, or
    None if fontTools isn't installed (in which case we skip cmap filtering
    and hope for the best -- the terminal will render tofu for any missing
    glyph)."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    tt = TTFont(font_path)
    cps = set(tt.getBestCmap().keys())
    tt.close()
    return cps


def enumerate_full_font(font_path: str) -> List[str]:
    """Enumerate every mapped codepoint in the font's cmap and return them
    as a list of single-character strings, filtered to what's usable in a
    monospace cell."""
    cps = _read_font_cmap(font_path)
    if cps is None:
        sys.exit(
            "--ramp full requires fonttools. Install with: pip install fonttools"
        )
    return [chr(cp) for cp in sorted(cps) if _is_keepable_codepoint(cp)]


def build_ramp(
    mode: str,
    font_path: str,
    font_px: int,
) -> Tuple[List[str], np.ndarray]:
    """Build a (chars, coverages) pair. `coverages` has shape (N, 4).

    Any character not present in the font's cmap is dropped -- otherwise it
    would render as .notdef (tofu) in the terminal even if we picked it.
    Characters listed in KNOWN_COVERAGES use their theoretical coverages
    instead of a rasterized measurement; this matters especially for block
    glyphs, whose measured coverage is systematically off due to em-square
    vs ascent+descent misalignment."""
    font = ImageFont.truetype(font_path, font_px)
    cell_w, cell_h = measure_cell(font)
    font_cmap = _read_font_cmap(font_path)

    if mode == "blocks":
        candidates = list(BLOCK_CHARS)
    elif mode == "blocks+ascii":
        seen = set()
        candidates = []
        for ch in BLOCK_CHARS + CURATED_ASCII:
            if ch in seen:
                continue
            seen.add(ch)
            candidates.append(ch)
    elif mode == "full":
        # Include blocks up front so space/full-block are guaranteed present
        # (some fonts don't have the quadrant blocks, and we want those
        #  degenerate cases covered).
        font_chars = enumerate_full_font(font_path)
        seen = set()
        candidates = []
        for ch in BLOCK_CHARS + font_chars:
            if ch in seen:
                continue
            seen.add(ch)
            candidates.append(ch)
    else:
        # Treat the mode string as a literal list of characters to use.
        candidates = list(mode)

    chars: List[str] = []
    covs: List[np.ndarray] = []
    skipped_missing = 0
    for ch in candidates:
        if font_cmap is not None and ord(ch) not in font_cmap:
            skipped_missing += 1
            continue
        if ch in KNOWN_COVERAGES:
            c = np.array(KNOWN_COVERAGES[ch], dtype=np.float32)
        else:
            c = rasterize_quadrants(ch, font, cell_w, cell_h)
            if c is None:
                continue
        chars.append(ch)
        covs.append(c)

    if skipped_missing:
        print(f"  {skipped_missing} chars skipped (missing from font cmap)")

    if not chars:
        sys.exit("Ramp is empty after filtering; check --ramp/--font.")
    return chars, np.stack(covs, axis=0)


# --- Image loading ----------------------------------------------------------

def _srgb_to_linear(x: np.ndarray) -> np.ndarray:
    """Convert sRGB values in [0, 1] to linear light in [0, 1] using the
    standard piecewise sRGB transfer curve."""
    return np.where(
        x <= 0.04045,
        x / 12.92,
        ((x + 0.055) / 1.055) ** 2.4,
    ).astype(np.float32)


def _linear_to_srgb(x: np.ndarray) -> np.ndarray:
    """Inverse of _srgb_to_linear."""
    x = np.clip(x, 0.0, 1.0)
    return np.where(
        x <= 0.0031308,
        12.92 * x,
        1.055 * (x ** (1.0 / 2.4)) - 0.055,
    ).astype(np.float32)


def load_image_as_subpixels(
    path: Path,
    cols: int,
    cell_w: int,
    cell_h: int,
) -> Tuple[np.ndarray, int]:
    """Load the image, compute rows from aspect ratio + font cell shape, and
    resize to (rows*2, cols*2) RGB float32 in [0, 1]. Returns (image, rows).

    Downsampling is done in linear light with a BOX filter:
      * BOX (exact area-averaging) has no negative lobes, so there's no
        ringing/overshoot around sharp edges -- important for logos and
        other non-photographic sources where overshoot produces visible
        halos of colors that don't exist in the original.
      * Averaging in linear light rather than sRGB avoids the dark-band
        muddiness you get when blending gamma-encoded values; mid-tone
        blends come out with the brightness and saturation your eye expects.
    """
    img = Image.open(path).convert("RGB")
    W, H = img.size
    cell_aspect = cell_h / cell_w   # typically ~2.0
    rows = max(1, round(cols * (H / W) / cell_aspect))
    sub_w, sub_h = cols * 2, rows * 2

    srgb = np.asarray(img, dtype=np.float32) / 255.0
    linear = _srgb_to_linear(srgb)

    # Resize each channel independently in PIL's 'F' (float32) mode so we
    # keep full precision through the downsample. BOX = pure area averaging.
    channels = []
    for ch in range(3):
        c_img = Image.fromarray(linear[..., ch], mode="F")
        c_resized = c_img.resize((sub_w, sub_h), Image.BOX)
        channels.append(np.asarray(c_resized, dtype=np.float32))
    linear_resized = np.stack(channels, axis=-1)

    return _linear_to_srgb(linear_resized), rows


# --- The fit: (char, fg, bg) per cell --------------------------------------

def fit_cells(
    image_2x: np.ndarray,
    coverages: np.ndarray,
    glyph_batch: int = 128,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each terminal cell, pick the best (glyph_index, fg, bg) from the
    ramp. Returns (idx[rows, cols], fg[rows, cols, 3], bg[rows, cols, 3]).

    Model:  rendered_quadrant_i = c_i * fg + (1 - c_i) * bg
    For each candidate glyph we solve a 2x2 normal-equation system per color
    channel for fg and bg, then score the residual against the target 2x2
    image block. Best score per cell wins.
    """
    H2, W2, _ = image_2x.shape
    rows, cols = H2 // 2, W2 // 2

    # T[r, c, i, ch] -- target color of quadrant i (tl, tr, bl, br) in RGB.
    T = np.stack([
        image_2x[0::2, 0::2, :],
        image_2x[0::2, 1::2, :],
        image_2x[1::2, 0::2, :],
        image_2x[1::2, 1::2, :],
    ], axis=2).astype(np.float32)

    N = coverages.shape[0]

    best_resid = np.full((rows, cols), np.inf, dtype=np.float32)
    best_idx = np.zeros((rows, cols), dtype=np.int32)
    best_fg = np.zeros((rows, cols, 3), dtype=np.float32)
    best_bg = np.zeros((rows, cols, 3), dtype=np.float32)

    rr, cc_ = np.meshgrid(np.arange(rows), np.arange(cols), indexing='ij')

    for n_start in range(0, N, glyph_batch):
        n_end = min(n_start + glyph_batch, N)
        c = coverages[n_start:n_end].astype(np.float32)   # (B, 4)
        ic = 1.0 - c
        B = c.shape[0]

        # Normal-matrix entries for the 2x2 system [[A, S], [S, D]]:
        A = (c * c).sum(axis=1)            # (B,)   sum c^2
        S = (c * ic).sum(axis=1)           # (B,)   sum c(1-c)
        D = (ic * ic).sum(axis=1)          # (B,)   sum (1-c)^2
        det = A * D - S * S                # (B,)

        # RHS (per channel): b0 = sum_i c_i T_i, b1 = sum_i (1-c_i) T_i.
        # Shape (B, rows, cols, 3).
        b0 = np.einsum('bi,rcig->brcg', c, T)
        b1 = np.einsum('bi,rcig->brcg', ic, T)

        safe_det = np.where(np.abs(det) < 1e-8, 1.0, det)
        Ar = A.reshape(B, 1, 1, 1)
        Sr = S.reshape(B, 1, 1, 1)
        Dr = D.reshape(B, 1, 1, 1)
        det_r = safe_det.reshape(B, 1, 1, 1)

        fg = ( Dr * b0 - Sr * b1) / det_r   # (B, rows, cols, 3)
        bg = (-Sr * b0 + Ar * b1) / det_r

        # Degenerate glyphs: all-zero coverage (space) -> bg = mean(T), fg = 0
        #                   all-one coverage (full block) -> fg = mean(T), bg = 0
        all_zero = (A < 1e-8)
        all_one = (D < 1e-8)
        if all_zero.any() or all_one.any():
            mean_T = T.mean(axis=2)           # (rows, cols, 3)
            if all_zero.any():
                fg[all_zero] = 0.0
                bg[all_zero] = mean_T
            if all_one.any():
                fg[all_one] = mean_T
                bg[all_one] = 0.0

        fg = np.clip(fg, 0.0, 1.0)
        bg = np.clip(bg, 0.0, 1.0)

        # Residual: sum over 4 quadrants & 3 channels of squared error.
        #   R[b, r, c, i, ch] = c[b,i] * fg[b,r,c,ch] + (1-c[b,i]) * bg[b,r,c,ch]
        R = (c[:, None, None, :, None] * fg[:, :, :, None, :]
             + ic[:, None, None, :, None] * bg[:, :, :, None, :])
        resid = ((R - T[None, ...]) ** 2).sum(axis=(3, 4))   # (B, rows, cols)

        # Merge this batch's best into the running best.
        batch_argmin = resid.argmin(axis=0)                       # (rows, cols)
        batch_min = np.take_along_axis(resid, batch_argmin[None], 0)[0]
        update = batch_min < best_resid
        if update.any():
            sel_fg = fg[batch_argmin, rr, cc_, :]
            sel_bg = bg[batch_argmin, rr, cc_, :]
            best_resid = np.where(update, batch_min, best_resid)
            best_idx = np.where(update, batch_argmin + n_start, best_idx)
            m = update[..., None]
            best_fg = np.where(m, sel_fg, best_fg)
            best_bg = np.where(m, sel_bg, best_bg)

    return best_idx, best_fg, best_bg


# --- Emit PowerShell --------------------------------------------------------

def _ansi_fg(r: int, g: int, b: int) -> str:
    return f"[38;2;{r};{g};{b}m"


def _ansi_bg(r: int, g: int, b: int) -> str:
    return f"[48;2;{r};{g};{b}m"


def build_art_text(
    chars: List[str],
    idx: np.ndarray,
    fg: np.ndarray,
    bg: np.ndarray,
) -> str:
    """Build the art as one UTF-8 string with PLACEHOLDER where each ANSI
    escape byte should end up. Runs of identical fg/bg are merged to cut
    escape-sequence noise."""
    rows, cols = idx.shape
    fg_u8 = np.round(fg * 255.0).astype(np.uint8)
    bg_u8 = np.round(bg * 255.0).astype(np.uint8)

    lines: List[str] = []
    for r in range(rows):
        parts: List[str] = []
        last_fg: Optional[Tuple[int, int, int]] = None
        last_bg: Optional[Tuple[int, int, int]] = None
        for c in range(cols):
            ch = chars[int(idx[r, c])]
            f = (int(fg_u8[r, c, 0]), int(fg_u8[r, c, 1]), int(fg_u8[r, c, 2]))
            b = (int(bg_u8[r, c, 0]), int(bg_u8[r, c, 1]), int(bg_u8[r, c, 2]))
            if b != last_bg:
                parts.append(PLACEHOLDER + _ansi_bg(*b))
                last_bg = b
            if f != last_fg:
                parts.append(PLACEHOLDER + _ansi_fg(*f))
                last_fg = f
            parts.append(ch)
        parts.append(PLACEHOLDER + "[0m")
        lines.append("".join(parts))
    return "\n".join(lines)


def emit_powershell(art_text: str, out_path: Path) -> None:
    # A line in the raw art always begins with PLACEHOLDER, so it can never
    # start with '@ (the here-string terminator). Guard anyway.
    for line in art_text.split("\n"):
        if line.startswith("'@"):
            raise RuntimeError("Art line collides with here-string terminator.")

    script = f"""# Generated by img2ansi.py -- truecolor ANSI art.
# Requires: Windows Terminal (or any VT-capable console). PS 5.1+ and PS 7+.
$__prev_enc = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {{
    $art = @'
{art_text}
'@
    [Console]::Write($art.Replace([char]0xE000, [char]27))
    [Console]::WriteLine()
}} finally {{
    [Console]::OutputEncoding = $__prev_enc
}}
"""
    # Write as UTF-8 with BOM so Windows PowerShell 5.1 reliably parses the
    # file as UTF-8 (it defaults to the local ANSI code page otherwise and
    # would mangle the \uE000 placeholder plus any non-ASCII ramp glyphs).
    out_path.write_bytes(b"\xef\xbb\xbf" + script.encode("utf-8"))


# --- Main -------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert an image to a truecolor ANSI PowerShell script.",
    )
    p.add_argument("input", type=Path, help="input image path")
    p.add_argument("output", type=Path, nargs="?",
                   help="output .ps1 path (defaults to INPUT with .ps1 suffix)")
    p.add_argument("--cols", type=int, required=True,
                   help="number of terminal columns")
    p.add_argument("--ramp", default="blocks+ascii",
                   help="blocks | blocks+ascii | full | <literal chars>")
    p.add_argument("--font", default=None,
                   help="monospace TTF path (default: auto-detect)")
    p.add_argument("--font-size", type=int, default=32,
                   help="glyph rasterization size in pixels (default: 32)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    font_path = args.font or find_default_font()
    out_path = args.output or args.input.with_suffix(".ps1")

    print(f"Font: {font_path}")
    print(f"Building ramp ({args.ramp!r}) at {args.font_size}px ...")
    chars, coverages = build_ramp(args.ramp, font_path, args.font_size)
    print(f"  ramp size: {len(chars)} glyphs")

    font = ImageFont.truetype(font_path, args.font_size)
    cell_w, cell_h = measure_cell(font)

    print(f"Loading image: {args.input}")
    image_2x, rows = load_image_as_subpixels(args.input, args.cols, cell_w, cell_h)
    print(f"  output size: {args.cols} cols x {rows} rows "
          f"(cell {cell_w}x{cell_h}, aspect {cell_h/cell_w:.2f})")

    print("Fitting cells ...")
    idx, fg, bg = fit_cells(image_2x, coverages)

    print(f"Emitting {out_path}")
    art = build_art_text(chars, idx, fg, bg)
    emit_powershell(art, out_path)
    print("Done.")


if __name__ == "__main__":
    main()
