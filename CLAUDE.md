# img-to-ansi — context for future sessions

## What this is

A converter that takes an image and emits a truecolor ANSI PowerShell script
(`.ps1`). Each terminal cell is fit to a `(character, fg, bg)` triple by
matching the character's four quadrant ink-coverages against the 2x2 block of
colors sampled from the source. For each candidate character the optimal
fg/bg is solved as a 2x2 least-squares system per color channel; the
character with the lowest residual wins.

Entry point: [img2ansi.py](img2ansi.py). Single file, ~370 lines.

## How to run

```powershell
# One-time setup
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Typical invocation
.\.venv\Scripts\python.exe .\img2ansi.py path\to\image.png --cols 100 --ramp blocks+ascii
# Then: . .\image.ps1   (from Windows Terminal / PowerShell 7+)
```

Three ramp modes, all worth trying per image: `blocks`, `blocks+ascii`, `full`
(every codepoint in the font's cmap that survives filtering). `blocks` is
coarsest but cleanest; `blocks+ascii` adds partial-coverage ASCII for texture;
`full` pulls in the whole font and can carry surprising detail in textured
regions but looks noisy in smooth ones. Different modes will also look better/worse
based upon target font, font size, and number of columns/rows.

## Non-obvious things that will bite you

**Windows Terminal has a "contrast adjustment" setting that silently rewrites
SGR colors.** It's called something like *Adjust indistinguishable colors
against background* under Appearance / Rendering. When enabled it overrides
truecolor values it deems too close to the cell bg, producing washed-out /
gray-tinted output that looks like an algorithm bug but isn't. If output
looks wrong despite the fit being correct, check this setting first. The
output PS1 script itself has no way to detect or override it.

**Consolas only contains 6 of the 16 Unicode quadrant blocks.** Present:
`space`, `█`, `▀`, `▄`, `▌`, `▐`. Missing (and rendering as `.notdef` tofu):
`▘ ▝ ▖ ▗ ▚ ▞ ▙ ▛ ▜ ▟`. We filter the ramp through the font's cmap (via
`fontTools`) so we never propose a glyph the font can't draw. Without this
filter, PIL silently rasterizes `.notdef` for the missing glyphs and the
ramp ends up with garbage coverage values.

**Block coverages are hardcoded, not measured** (see `KNOWN_COVERAGES`).
Even for blocks the font *does* have, PIL's rasterization measures them as
~0.85 / ~0.15 instead of 1.0 / 0.0, because the font's em-square middle
doesn't coincide with `(ascent+descent)/2`. When the LSQ fit sees coverage
of 0.85 it pushes fg/bg *beyond* the true target colors to compensate, and
the terminal then renders the glyph at actual 100% coverage — result is
oversaturated output with visible haloing. Always use theoretical coverages
for anything with a mathematically defined ink layout.

**Downsample happens in linear light with a BOX filter.** `Image.LANCZOS`
introduces ringing halos around hard edges (logos, UI elements); BOX is pure
area-averaging with no negative lobes. And averaging sRGB bytes linearly
produces muddy mid-tones; converting to linear → resize → back to sRGB keeps
the perceived brightness/saturation correct. Both matter; together they
remove most of what previously looked like "colors off" issues.

**PS1 output format.** The art is embedded as a single-quoted here-string
with `\uE000` (PUA codepoint) as a placeholder for the ESC byte, substituted
once at runtime via `.Replace([char]0xE000, [char]27)`. This way image
characters pass through literally — no worrying about PowerShell's
`$`/`` ` ``/`"` escape rules — and it works in both PS 5.1 and PS 7+. The
file is written as UTF-8 with BOM so Windows PowerShell 5.1 doesn't fall
back to the local ANSI code page and mangle the placeholder.

## Validating output independent of the terminal

The fit produces `(idx, fg, bg)` arrays. You can reconstruct what the terminal
*should* display by painting a 2x2 pixel grid per cell using the theoretical
coverage mask for the chosen glyph — `debug_reconstruct.png` shows the
algorithm's actual output. If that image looks right but the terminal looks
wrong, the issue is terminal-side (see the WT gotcha above). Similarly
`debug_resized.png` shows what the fit sees as input; if *that* looks wrong,
the issue is upstream in load/resize. Both diagnostics were written ad-hoc
during debugging; worth keeping as a pattern.

## Collaboration notes

The user is deeply fluent in C# but less so in Python — debugging on their
end will be slower in Python. Favor readable, explicit code over terse
NumPy tricks when the two compete. Vectorized math is fine (commented
well); excessive einsum or broadcast gymnastics is not.

Potential future directions the user has mentioned interest in: more
features, probably on top of this same scaffold. Nothing specific committed.
See `TODO.md` for some of the user's thoughts after our first session. 
