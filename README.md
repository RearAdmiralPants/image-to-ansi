# Image To ANSI

Convert an image into a truecolor ANSI PowerShell script. Each terminal cell
is fit to a `(character, fg, bg)` triple by a least-squares match against a
configurable character ramp.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Usage

```powershell
.\.venv\Scripts\python.exe .\img2ansi.py INPUT [OUTPUT] --cols N [--ramp MODE] [--font PATH]
```

Ramp modes:
- `blocks` — unicode block/half-block/quadrant characters only
- `blocks+ascii` — blocks + curated ASCII for intermediate coverages
- `full` — every codepoint present in the font (requires `fonttools`)
- any literal string — use exactly those characters as the ramp

Examples:

```powershell
.\.venv\Scripts\python.exe .\img2ansi.py img.png img_blocks.ps1 --cols 100 --ramp blocks
.\.venv\Scripts\python.exe .\img2ansi.py img.png img_ascii.ps1  --cols 100 --ramp blocks+ascii
.\.venv\Scripts\python.exe .\img2ansi.py img.png img_full.ps1   --cols 100 --ramp full

. .\img_blocks.ps1
```

## Important: Windows Terminal contrast adjustment

Windows Terminal has a setting (under Appearance / Rendering, usually named
*Adjust indistinguishable colors against background* or similar) that
rewrites SGR truecolor values it judges too close to the cell background.
**Turn this off** before judging output accuracy — otherwise the terminal
will override the exact colors the script emits and the result will look
washed out or tinted regardless of what the converter did.

## Requires

PowerShell 5.1+ or PowerShell 7+ in a VT-capable terminal (Windows Terminal
recommended). Generated scripts set UTF-8 output encoding themselves and
restore the previous encoding on exit.
