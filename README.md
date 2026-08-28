# Blender

## DaVinci Resolve scripts

`scripts/zraw_to_rec709.py` — run it once and every Z CAM ZRAW clip on the
current timeline (or every timeline, with `--all-timelines`) comes out
normalised to Rec.709, ready for further colour grading. No manual Color
page work required. The source ZRAW file and its Camera Raw/RAW decode
metadata are never touched.

Two modes:
- **RCM mode (default)** — tags each ZRAW clip's Resolve Color Management
  "Input Color Space", so Resolve's own colour engine inserts the correct
  transform automatically before Node 1. Requires Color Management to be
  enabled in Project Settings (the script checks and tells you if it isn't).
- **`--lut PATH` mode** — applies a 3D LUT (e.g. Z CAM's official Z-Log2 ->
  Rec.709 technical LUT) directly to Node 1 of every ZRAW clip. Works
  without Color Management enabled.

See the docstring at the top of the script for full details and other
flags (`--dry-run`, `--debug`, `--input-color-space`).
