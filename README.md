# Blender

## DaVinci Resolve scripts

`scripts/zraw_to_rec709.py` — normalises Z CAM ZRAW footage to Rec.709
inside DaVinci Resolve Studio 20 using a real **Color Space Transform (CST)
node** in the Color page, not by rewriting the clip's RAW decode metadata.

Nothing on the source ZRAW file or the Media Pool clip's Camera Raw
settings is touched. The script batch-applies a Color Space Transform
node graph (built once by hand and exported as a `.drx`) to every Z CAM
ZRAW clip on the timeline via `TimelineItem.ApplyGradeFromDRX()`, so the
result is a normal, visible, editable node — same as if you'd dragged a
PowerGrade onto each clip yourself.

See the docstring at the top of the script for the one-time setup (build
the CST node + export the `.drx`) and usage (`--dry-run`,
`--all-timelines`, etc.).
