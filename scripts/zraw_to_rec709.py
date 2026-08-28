#!/usr/bin/env python3
"""
zraw_to_rec709.py

DaVinci Resolve Studio 20 script that normalises every Z CAM clip shot in
Z-Log2 on the current timeline to Rec.709, fully automatically -- no
per-clip work in the Color page, no pre-built grade to apply by hand. This
covers both raw ZRAW footage and Z-Log2 baked into a conventional codec
(e.g. H.265/HEVC .MOV, which is what many Z CAM cameras record by default
-- the codec changes, the Z-Log2 colour science underneath doesn't). Run
it, and every matching clip on the timeline comes out Rec.709-normalised,
ready for further colour grading. The source media file and its metadata
are never touched.

WHY TWO MODES
-------------
The Resolve scripting API has no call to insert an arbitrary OFX filter
(such as the Color Space Transform effect) into a clip's node graph, so
"the script builds a brand-new CST node from nothing" isn't something the
API supports. What IS supported, and used here, are two genuinely automatic,
non-destructive ways to get the same *result* -- clips normalised to
Rec.709 via a real transform, not a metadata rewrite:

  1. RCM mode (default) -- tags each ZRAW clip's "Input Color Space" (a
     Resolve Color Management property stored in the project database, not
     in the file). With Color Management enabled and the timeline/output
     colour space set to Rec.709, Resolve's own colour engine automatically
     inserts the correct transform for every clip before Node 1, using
     Blackmagic's calibrated Z CAM colour science. This is the standard,
     professional way to batch-normalise camera-native footage for editors/
     grading and needs zero manual Color page work per clip.

     Prerequisite: Color Management must be ON for the project (Project
     Settings > Color Management > Color science = "DaVinci YRGB Color
     Managed"). This is a one-time, project-wide setting -- the script
     checks it and tells you plainly if it's off rather than flipping a
     project-wide pipeline setting behind your back (that could disrupt
     other, unrelated clips/grades already in the project).

  2. LUT mode (--lut PATH) -- applies a 3D LUT directly to Node 1 of every
     ZRAW clip's node graph via the documented Graph.SetLUT() call. This
     produces a literal, visible "node with a transform on it" per clip,
     works regardless of whether Color Management is on, and needs no
     project-wide setting change. Use Z CAM's own official Z-Log2 ->
     Rec.709 technical LUT for this (published by Z CAM for exactly this
     purpose) -- this script does not fabricate a LUT itself, since an
     incorrect transfer-function/matrix guess would bake in wrong colour.

Both modes touch nothing on disk and nothing on the source clip's RAW
decode settings -- only the Color page's per-clip Input Color Space tag
(mode 1) or Node 1's LUT (mode 2), both fully visible and removable from
Resolve's UI at any time.

INSTALLATION
------------
Copy this file into Resolve's Utility Scripts folder so it shows up under
Workspace > Scripts:

  Mac:     ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
  Windows: %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\
  Linux:   ~/.local/share/DaVinciResolve/Fusion/Scripts/Utility/

Then run it from Workspace > Scripts > zraw_to_rec709, or as an external
script with Resolve open and External Scripting enabled (Preferences >
System > General).

USAGE
-----
  python3 zraw_to_rec709.py                       # RCM mode, current timeline
  python3 zraw_to_rec709.py --all-timelines
  python3 zraw_to_rec709.py --dry-run
  python3 zraw_to_rec709.py --lut ~/ZCAM_ZLog2_to_Rec709.cube
  python3 zraw_to_rec709.py --debug                # dump properties of the
                                                     # first ZRAW clip found,
                                                     # to check/adjust the
                                                     # Input Color Space
                                                     # candidate strings below
                                                     # if the defaults don't
                                                     # match your Resolve
                                                     # version
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


ZRAW_EXTENSIONS = (".zraw",)
ZRAW_MARKERS = ("zraw", "z-raw", "z cam", "zcam")
# Checked only against real metadata fields (Camera Type/Codec/Format/Gamma/
# Color Space), never filenames -- catches Z CAM's Z-Log2 gamma even when
# it's wrapped in a conventional codec (e.g. H.265/HEVC MOV, which is what
# many Z CAM cameras record by default) rather than shot as raw ZRAW. Same
# colour science, just not raw sensor data, so the same fix applies. A bare
# "log2" is deliberately NOT checked against filenames -- too easy to false-
# match an unrelated file that happens to contain that substring.
LOG2_MARKERS = ("zlog2", "z log2", "z-log2", "log2")
METADATA_ONLY_PROPS = ["Camera Type", "Codec", "Format", "Gamma", "Color Space", "Input Color Space"]

# Candidate strings for the RCM "Input Color Space" tag on a Z CAM ZRAW
# clip. Exact wording has varied across Resolve releases -- each is tried
# in order and verified by reading the property back; use --input-color-space
# to override outright, or --debug to see the exact current value/options
# on your build.
INPUT_COLOR_SPACE_CANDIDATES = [
    "Z CAM ZRAW Wide Gamut",
    "ZRAW Wide Gamut",
    "Z CAM Z-Log2",
    "Z Log2",
    "ZLog2",
]


def is_zraw_clip(media_pool_item):
    """Best-effort, read-only detection of Z CAM footage shot in Z-Log2 --
    whether it's raw ZRAW or Z-Log2 baked into a conventional codec (e.g.
    H.265/HEVC .MOV, which is what many Z CAM cameras record by default).
    Only reads clip properties -- never writes anything."""
    if media_pool_item is None:
        return False

    # Filename/clip name: only the unambiguous ZRAW/Z CAM markers, plus the
    # .zraw extension.
    for prop in ("File Name", "Clip Name"):
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

    # Real metadata fields: also check for a Z-Log2 gamma/colour space tag,
    # which is how Z-Log2-in-H.265 footage (not raw) shows up.
    for prop in METADATA_ONLY_PROPS:
        try:
            value = media_pool_item.GetClipProperty(prop)
        except Exception:
            continue
        if not value:
            continue
        text = str(value).lower()
        if any(marker in text for marker in ZRAW_MARKERS):
            return True
        if any(marker in text for marker in LOG2_MARKERS):
            return True

    return False


def iter_video_timeline_items(timeline):
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


def color_management_enabled(project):
    """Best-effort check of whether RCM (Color Management) is on for this
    project. Returns True/False/None (None = could not determine)."""
    try:
        mode = project.GetSetting("colorScienceMode")
    except Exception:
        return None
    if not mode:
        return None
    return "colormanaged" in str(mode).replace(" ", "").lower()


def dump_clip_properties(media_pool_item):
    print("\n--- Clip property dump (for adjusting this script) ---")
    try:
        props = media_pool_item.GetClipProperty()
        for key in sorted(props.keys()):
            print(f"  {key!r}: {props[key]!r}")
    except Exception as exc:
        print(f"  Could not enumerate properties: {exc}")
    print("--- end dump ---\n")


def set_property_with_fallback(media_pool_item, prop_name, candidates):
    for value in candidates:
        try:
            media_pool_item.SetClipProperty(prop_name, value)
        except Exception:
            continue
        try:
            applied = media_pool_item.GetClipProperty(prop_name)
        except Exception:
            applied = None
        if applied and str(applied).strip().lower() == str(value).strip().lower():
            return applied
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--lut", default=None,
        help="Path to a Z-Log2/ZRAW-Wide-Gamut -> Rec.709 3D LUT (.cube). "
             "If given, applies it directly to Node 1 of every ZRAW clip "
             "instead of using RCM Input Color Space tagging.",
    )
    parser.add_argument(
        "--input-color-space", default=None,
        help="Override the RCM 'Input Color Space' value to tag ZRAW clips "
             "with (RCM mode only). Use --debug to see the exact string your "
             "Resolve build expects if the built-in candidates don't stick.",
    )
    parser.add_argument(
        "--all-timelines", action="store_true",
        help="Process every timeline in the project instead of just the current one.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report which clips would be changed without changing anything.",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Print all clip properties for the first ZRAW clip found, then continue.",
    )
    args = parser.parse_args()

    lut_path = None
    if args.lut:
        lut_path = os.path.abspath(os.path.expanduser(args.lut))
        if not os.path.isfile(lut_path):
            print(f"ERROR: LUT file not found: {lut_path}")
            sys.exit(1)

    resolve = get_resolve()
    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject()
    if project is None:
        print("No project is currently open in Resolve. Open a project and try again.")
        sys.exit(1)

    if lut_path is None:
        rcm_on = color_management_enabled(project)
        if rcm_on is False:
            print(
                "Color Management is OFF for this project. RCM mode needs it on "
                "to insert the automatic Input Color Space -> timeline transform.\n"
                "Enable it once via Project Settings > Color Management > "
                "Color science = 'DaVinci YRGB Color Managed' (and set Timeline/"
                "Output Color Space to Rec.709 there), then re-run this script.\n"
                "Alternatively, run with --lut PATH to apply a LUT directly to "
                "Node 1 instead, which works without Color Management."
            )
            sys.exit(1)
        elif rcm_on is None:
            print(
                "Could not confirm Color Management is enabled from the API -- "
                "proceeding, but if nothing changes, check Project Settings > "
                "Color Management yourself, or use --lut instead.\n"
            )

    timelines = get_timelines(project, args.all_timelines)
    if not timelines:
        print("No timeline found (open/create a timeline with your ZRAW clips cut in).")
        sys.exit(1)

    input_cs_candidates = (
        [args.input_color_space] if args.input_color_space else INPUT_COLOR_SPACE_CANDIDATES
    )

    total_items = 0
    zraw_items = 0
    updated = 0
    skipped = 0
    failed = 0
    debugged = False
    tagged_source_clips = set()  # avoid re-tagging the same source clip twice in RCM mode

    for timeline in timelines:
        print(f"Timeline: {timeline.GetName()}")
        for item in iter_video_timeline_items(timeline):
            total_items += 1
            media_pool_item = item.GetMediaPoolItem()
            if not is_zraw_clip(media_pool_item):
                continue

            zraw_items += 1
            clip_name = item.GetName()

            if args.debug and not debugged:
                dump_clip_properties(media_pool_item)
                debugged = True

            if args.dry_run:
                print(f"  [would fix] {clip_name}")
                continue

            if lut_path is not None:
                try:
                    graph = item.GetNodeGraph()
                    ok = graph.SetLUT(1, lut_path)
                except Exception as exc:
                    print(f"  [FAILED] {clip_name}: exception applying LUT ({exc})")
                    failed += 1
                    continue
                if ok:
                    print(f"  [fixed]  {clip_name}: LUT applied to Node 1 ({os.path.basename(lut_path)})")
                    updated += 1
                else:
                    print(f"  [FAILED] {clip_name}: SetLUT returned False")
                    failed += 1
                continue

            # RCM mode: tag the underlying source clip once.
            clip_id = media_pool_item.GetClipProperty("File Path") or media_pool_item.GetClipProperty("Clip Name")
            if clip_id in tagged_source_clips:
                skipped += 1
                continue

            applied = set_property_with_fallback(media_pool_item, "Input Color Space", input_cs_candidates)
            if applied:
                print(f"  [fixed]  {clip_name}: Input Color Space -> {applied}")
                updated += 1
                tagged_source_clips.add(clip_id)
            else:
                print(
                    f"  [FAILED] {clip_name}: could not confirm Input Color Space tag. "
                    f"Run with --debug or pass --input-color-space explicitly."
                )
                failed += 1

    print("\nSummary:")
    print(f"  timeline clips scanned: {total_items}")
    print(f"  ZRAW clips found:       {zraw_items}")
    if args.dry_run:
        print("  (dry run -- nothing was changed)")
    else:
        print(f"  fixed:                  {updated}")
        print(f"  skipped (already done): {skipped}")
        print(f"  failed:                 {failed}")
        if failed:
            print(
                "\nSome clips could not be confirmed. Run with --debug to inspect "
                "GetClipProperty() output for a ZRAW clip and adjust "
                "INPUT_COLOR_SPACE_CANDIDATES at the top of this script, or pass "
                "--input-color-space / --lut explicitly."
            )


if __name__ == "__main__":
    main()
