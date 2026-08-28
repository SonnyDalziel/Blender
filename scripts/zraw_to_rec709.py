#!/usr/bin/env python3
"""
zraw_to_rec709.py

DaVinci Resolve Studio 20 script that normalises Z CAM ZRAW footage to
Rec.709 using a real Color Space Transform (CST) node in the Color page --
NOT by rewriting RAW decode metadata on the clip or file.

This is fully non-destructive:
  * The original ZRAW media file on disk is never touched.
  * The Media Pool clip's RAW decode settings (Camera Raw tab / Color Space
    / Gamma properties) are left exactly as they are.
  * The only thing that changes is the per-timeline-instance node graph in
    the Color page: a Color Space Transform node (Input: Z CAM's native
    space, Output: Rec.709) is inserted, exactly as if you had built it by
    hand with the CST OFX. It shows up as a normal, clickable, editable node
    -- you can tweak it, disable it, or delete it per clip like any other
    grade.

WHY A TWO-STEP WORKFLOW
------------------------
The Resolve scripting API does not expose "add a Color Space Transform OFX
to this node" as a callable. What it DOES expose is
TimelineItem.ApplyGradeFromDRX(), which applies a previously saved grade
(a .drx file) onto a timeline clip's node graph. So the workflow is:

  STEP 1 (one-time, done by hand in Resolve):
    1. Put one Z CAM ZRAW clip on a timeline and open the Color page.
    2. On Node 1, add a Color Space Transform (Effects Library > OpenFX >
       ResolveFX Color > Color Space Transform, or right-click the node >
       Add Node > ... in older versions it's under the same OFX list).
    3. Set the CST's:
         Input Color Space / Gamma  -> whatever entry matches your camera,
                                        e.g. "Z CAM ZRAW Wide Gamut" /
                                        "Z Log2" (check the exact wording
                                        in the dropdown against the "Color
                                        Space" / "Gamma" fields shown in
                                        that clip's Camera Raw tab under
                                        Clip Attributes, so the CST input
                                        matches how the clip is actually
                                        being decoded).
         Output Color Space / Gamma -> "Rec.709" / "Rec.709" (or "Rec.709
                                        Gamma 2.4", matching your timeline
                                        colour space).
    4. With that clip selected, grab a still (right-click the thumbnail
       timeline at the top of the Color page > "Grab Still", or press the
       grab-still button). This adds a still to the Gallery.
    5. In the Gallery, right-click that still > Export... and save it as a
       .drx file somewhere on disk, e.g. ~/zraw_to_rec709.drx.

  STEP 2 (this script, repeatable):
    Run this script with --drx pointing at that .drx file. It walks the
    current timeline (or every timeline with --all-timelines), finds every
    clip whose source media is Z CAM ZRAW, and calls
    ApplyGradeFromDRX() on each one -- so every ZRAW clip gets the same CST
    node graph applied, in one pass, without you dragging a PowerGrade onto
    each clip by hand.

NOTE: ApplyGradeFromDRX() applies the *entire* saved node graph from the
.drx, replacing whatever grade is currently on that clip instance. Run this
early in your workflow (before secondary grading), or keep the source .drx
to just the single CST node so there's nothing else to clobber.

INSTALLATION
------------
Copy this file into Resolve's Utility Scripts folder so it shows up under
Workspace > Scripts:

  Mac:     ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
  Windows: %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\
  Linux:   ~/.local/share/DaVinciResolve/Fusion/Scripts/Utility/

Then run it from Workspace > Scripts > zraw_to_rec709, or as an external
script (python3 zraw_to_rec709.py --drx /path/to/grade.drx) with Resolve
open and External Scripting enabled (Preferences > System > General).

USAGE
-----
  python3 zraw_to_rec709.py --drx ~/zraw_to_rec709.drx
  python3 zraw_to_rec709.py --drx ~/zraw_to_rec709.drx --dry-run
  python3 zraw_to_rec709.py --drx ~/zraw_to_rec709.drx --all-timelines
  python3 zraw_to_rec709.py --drx ~/zraw_to_rec709.drx --grade-mode source-tc
"""

import argparse
import os
import sys


# ---------------------------------------------------------------------------
# Connect to Resolve, whether we're running inside Resolve (Workspace >
# Scripts, where a global "resolve" object is injected) or as an external
# script (where we have to go through the DaVinciResolveScript module).
# ---------------------------------------------------------------------------
def get_resolve():
    try:
        return resolve  # noqa: F821  (injected by Resolve when run as a menu script)
    except NameError:
        pass

    try:
        import DaVinciResolveScript as dvr_script
        resolve_obj = dvr_script.scriptapp("Resolve")
        if resolve_obj is None:
            raise RuntimeError(
                "Could not connect to DaVinci Resolve. Make sure Resolve is "
                "running and External Scripting is enabled "
                "(Preferences > System > General > External scripting using)."
            )
        return resolve_obj
    except ImportError as exc:
        raise RuntimeError(
            "Could not import DaVinciResolveScript. Run this from inside "
            "Resolve's Workspace > Scripts menu, or set PYTHONPATH to "
            "include Resolve's Scripting/Modules folder."
        ) from exc


# gradeMode values accepted by TimelineItem.ApplyGradeFromDRX()
GRADE_MODES = {
    "no-keyframes": 0,
    "source-tc": 1,
    "start-frames": 2,
}

ZRAW_EXTENSIONS = (".zraw",)
ZRAW_MARKERS = ("zraw", "z-raw", "z cam", "zcam")


def is_zraw_clip(media_pool_item):
    """Best-effort, read-only detection of a Z CAM ZRAW source clip.
    Only reads clip properties -- never writes anything."""
    if media_pool_item is None:
        return False
    props_to_check = ["Camera Type", "Codec", "Format", "File Name", "Clip Name"]
    for prop in props_to_check:
        try:
            value = media_pool_item.GetClipProperty(prop)
        except Exception:
            continue
        if not value:
            continue
        text = str(value).lower()
        if any(marker in text for marker in ZRAW_MARKERS):
            return True
        if any(text.endswith(ext) for ext in ZRAW_EXTENSIONS):
            return True
    return False


def iter_video_timeline_items(timeline):
    """Yield every video-track TimelineItem on a timeline."""
    track_count = timeline.GetTrackCount("video")
    for track_index in range(1, track_count + 1):
        for item in timeline.GetItemListInTrack("video", track_index):
            yield item


def get_timelines(project, all_timelines):
    if all_timelines:
        count = project.GetTimelineCount()
        timelines = [project.GetTimelineByIndex(i) for i in range(1, count + 1)]
        return [t for t in timelines if t is not None]
    current = project.GetCurrentTimeline()
    if current is None:
        return []
    return [current]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--drx", required=True,
        help="Path to the .drx grade (Node 1 = Color Space Transform to Rec.709) "
             "exported from the Gallery, per the STEP 1 instructions above.",
    )
    parser.add_argument(
        "--all-timelines", action="store_true",
        help="Apply to every timeline in the project instead of just the current one.",
    )
    parser.add_argument(
        "--grade-mode", choices=sorted(GRADE_MODES), default="no-keyframes",
        help="Alignment mode passed to ApplyGradeFromDRX (default: no-keyframes).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report which timeline clips would be graded without changing anything.",
    )
    args = parser.parse_args()

    drx_path = os.path.abspath(os.path.expanduser(args.drx))
    if not os.path.isfile(drx_path):
        print(f"ERROR: .drx file not found: {drx_path}")
        print("Complete STEP 1 in this script's docstring first, or check --drx.")
        sys.exit(1)

    resolve = get_resolve()
    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject()
    if project is None:
        print("No project is currently open in Resolve. Open a project and try again.")
        sys.exit(1)

    timelines = get_timelines(project, args.all_timelines)
    if not timelines:
        print("No timeline found (open/create a timeline with your ZRAW clips cut in).")
        sys.exit(1)

    grade_mode = GRADE_MODES[args.grade_mode]

    total_items = 0
    zraw_items = 0
    applied = 0
    failed = 0

    for timeline in timelines:
        print(f"Timeline: {timeline.GetName()}")
        for item in iter_video_timeline_items(timeline):
            total_items += 1
            media_pool_item = item.GetMediaPoolItem()
            if not is_zraw_clip(media_pool_item):
                continue

            zraw_items += 1
            clip_name = item.GetName()

            if args.dry_run:
                print(f"  [would apply CST] {clip_name}")
                continue

            try:
                ok = item.ApplyGradeFromDRX(drx_path, grade_mode)
            except Exception as exc:
                print(f"  [FAILED] {clip_name}: exception applying grade ({exc})")
                failed += 1
                continue

            if ok:
                print(f"  [applied] {clip_name}: CST node graph applied from {os.path.basename(drx_path)}")
                applied += 1
            else:
                print(f"  [FAILED] {clip_name}: ApplyGradeFromDRX returned False")
                failed += 1

    print("\nSummary:")
    print(f"  timeline clips scanned: {total_items}")
    print(f"  ZRAW clips found:       {zraw_items}")
    if args.dry_run:
        print("  (dry run -- nothing was changed)")
    else:
        print(f"  grade applied:          {applied}")
        print(f"  failed:                 {failed}")
        if applied:
            print(
                "\nOpen the Color page on any updated clip to see the new "
                "Color Space Transform node -- it's a normal node you can "
                "click, tweak, or remove, and nothing on the source ZRAW "
                "file or its clip metadata was modified."
            )


if __name__ == "__main__":
    main()
