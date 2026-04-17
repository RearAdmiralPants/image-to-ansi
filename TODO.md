# TODO

(Potentially)

## Cross-Platform

- **Bash/Shell Script** In addition to PowerShell scripts, add a bash/shell script output option primarily for Linux but potentially functional in Cygwin/git-bash and (much more simply) WSL
- **ANSI file output** To the extent possible considering a cross-platform environment (may need settings for how bash interprets an `esc` sequence vs. how PowerShell does so when simply `cat`-ing a file), generate plain `.ansi` files for custom use

## Font Manipulation

- **Changing Font Rendering** (I'm uncertain this is possible in PowerShell and I suspect it is not possible in any popular Linux shells, though there may be plug-ins or specific terminal emulators that support it) Instruct the use of a particular font and font size in the script output to better accomodate ANSI output of greater columns/rows
    - Alternatively, build a quick GUI which renders a given `.ansi` file, potentially using an accompanying metadata file (`filename.ansi.json` or `filename.ansi.yaml`) to specify the font and size to use by default for that particular `.ansi` file, with the ability to override in the GUI app

## (Probably Overkill) Video Support

- **Custom Video Files** Given that:
    - Videos are just a series of (extrapolated, in some cases) bitmaps frames;
    - We can store multiple "frames" of ANSI data separated by some delimiter in a lengthy text string;
    - The resulting lengthy text string ought to be very highly compressible;
    - It would be straightforward to create a file format representing an ASCII video in ANSI color (sound would initially be absent);
    - It would be straightforward to create a GUI application that would attempt to play back that file format at some framerate specified in a metadata section;
    - Sound may be possible to add in an interleaved fashion alongside the video data for appropriate streaming and synchronization, or alternatively as a separate resource stream in a multipart/packaged file format and synchronized via traditional timestamp / framestamp means.
    - **HOWEVER**
        - I believe there are shaders which can do all of this in realtime on any consumer GPU from the last decade or more, which, if they could produce effectively the same output, configurable to the same extent, would make the feature moot other than to build for "fun"
        - The shader would need to be capable of operating on any video stream instead of only on e.g. "Rendered" content, but given there are OpenGL/Direct3D output modes for most consumer video software out there, this is probably not an obstacle in any way at all