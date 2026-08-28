#!/usr/bin/env python3
"""
zraw_to_rec709.py

DaVinci Resolve Studio 20 script that finds Z CAM ZRAW clips in the current
project's Media Pool and corrects their RAW decode settings so they are
interpreted as Rec.709 instead of the camera-native ZRAW colour space/gamma
(ZRAW Wide Gamut / ZLog2, depending on camera and firmware).

WHAT IT DOES
------------
For every clip recognised as Z CAM ZRAW (by camera metadata, codec name or
file extension) it:
  1. Switches the clip's RAW decode source to "Clip" so a per-clip override
     is honoured instead of the project-wide Camera RAW setting.
  2. Sets the RAW decode "Color Space" and "Gamma" to Rec.709.

It does NOT touch grades, LUTs, or Resolve Color Management (RCM) input
colour space tags -- it only corrects the raw debayer so the clip's baseband
image is already Rec.709 straight off the sensor data, which is the fix
requested for ZRAW footage that comes in looking flat/log or off-colour
because it was decoded with the camera-native colour space instead of
Rec.709.

INSTALLATION
------------
Copy this file into Resolve's Utility Scripts folder so it shows up under
Workspace > Scripts:

  Mac:     ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
  Windows: %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\
  Linux:   ~/.local/share/DaVinciResolve/Fusion/Scripts/Utility/

Then, with your project open in Resolve, run it from
Workspace > Scripts > zraw_to_rec709.

It also runs fine as a standalone external script (python3 zraw_to_rec709.py)
as long as Resolve is open and "External scripting using" is enabled in
Resolve > Preferences > System > General.

USAGE
-----
  python3 zraw_to_rec709.py                 # scan whole media pool, apply fix
  python3 zraw_to_rec709.py --dry-run        # report only, change nothing
  python3 zraw_to_rec709.py --gamma "Rec.709 Gamma 2.4"
  python3 zraw_to_rec709.py --debug          # dump clip properties for the
                                              # first ZRAW clip found, to help
                                              # adjust property names/values
                                              # for your Resolve version if
                                              # the defaults below don't match
"""

import argparse
import sys

# ---------------------------------------------------------------------------
# Connect to Resolve, whether we're running inside Resolve (Workspace >
# Scripts, where a global "resolve" object is injected) or as an external
# script (where we have to go through the DaVinciResolveScript module).
# ---------------------------------------------------------------------------
def get_resolve():
    try:
        # Running from inside Resolve's Scripts menu.
        return resolve  # noqa: F821
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


# Candidate camera-raw decode settings. Resolve's exact accepted strings for
# the "Color Space" / "Gamma" RAW decode dropdowns have shifted slightly
# across versions, so each target is tried as a list of fallbacks; the first
# one SetClipProperty() actually accepts (verified by reading it back) wins.
REC709_COLOR_SPACE_CANDIDATES = ["Rec.709", "Rec709", "Rec 709", "REC709"]
REC709_GAMMA_CANDIDATES = ["Rec.709", "Rec709", "Rec 709", "REC709"]

# Property names used to recognise a Z CAM ZRAW clip. Different Resolve
# versions/camera firmwares populate slightly different metadata fields, so
# every clip is checked against all of them.
ZRAW_EXTENSIONS = (".zraw",)
ZRAW_MARKERS = ("zraw", "z-raw", "z cam", "zcam")


def is_zraw_clip(clip):
    """Best-effort detection of a Z CAM ZRAW media pool clip."""
    props_to_check = ["Camera Type", "Codec", "Format", "File Name", "Clip Name"]
    for prop in props_to_check:
        try:
            value = clip.GetClipProperty(prop)
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


def set_property_with_fallback(clip, prop_name, candidates):
    """Try each candidate value for prop_name until GetClipProperty confirms
    it stuck. Returns the value that was actually applied, or None."""
    for value in candidates:
        try:
            clip.SetClipProperty(prop_name, value)
        except Exception:
            continue
        applied = None
        try:
            applied = clip.GetClipProperty(prop_name)
        except Exception:
            pass
        if applied and str(applied).strip().lower() == value.strip().lower():
            return applied
    # Some Resolve builds don't echo back an exact string match even though
    # the set succeeded (e.g. it stores "Rec.709 Gamma 2.4" after being sent
    # "Rec.709"). Re-read once more and accept whatever is there now if it
    # at least contains "709".
    try:
        applied = clip.GetClipProperty(prop_name)
        if applied and "709" in str(applied):
            return applied
    except Exception:
        pass
    return None


def walk_clips(folder, clips=None):
    """Recursively collect every MediaPoolItem under a Media Pool folder."""
    if clips is None:
        clips = []
    clips.extend(folder.GetClipList())
    for sub_folder in folder.GetSubFolderList():
        walk_clips(sub_folder, clips)
    return clips


def dump_clip_properties(clip):
    print("\n--- Clip property dump (for adjusting this script) ---")
    try:
        props = clip.GetClipProperty()
        for key in sorted(props.keys()):
            print(f"  {key!r}: {props[key]!r}")
    except Exception as exc:
        print(f"  Could not enumerate properties: {exc}")
    print("--- end dump ---\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--color-space", default=None,
        help="Override the target RAW decode Color Space value (default: try Rec.709 variants).",
    )
    parser.add_argument(
        "--gamma", default=None,
        help="Override the target RAW decode Gamma value (default: try Rec.709 variants).",
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

    color_space_candidates = [args.color_space] if args.color_space else REC709_COLOR_SPACE_CANDIDATES
    gamma_candidates = [args.gamma] if args.gamma else REC709_GAMMA_CANDIDATES

    resolve = get_resolve()
    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject()
    if project is None:
        print("No project is currently open in Resolve. Open a project and try again.")
        sys.exit(1)

    media_pool = project.GetMediaPool()
    root_folder = media_pool.GetRootFolder()
    all_clips = walk_clips(root_folder)

    print(f"Scanned {len(all_clips)} clip(s) in project '{project.GetName()}'.")

    zraw_clips = [c for c in all_clips if is_zraw_clip(c)]
    print(f"Found {len(zraw_clips)} Z CAM ZRAW clip(s).")

    if not zraw_clips:
        print("Nothing to do.")
        return

    if args.debug:
        dump_clip_properties(zraw_clips[0])

    updated, skipped, failed = 0, 0, 0

    for clip in zraw_clips:
        name = clip.GetClipProperty("Clip Name") or clip.GetName()

        current_color_space = None
        current_gamma = None
        try:
            current_color_space = clip.GetClipProperty("Color Space")
            current_gamma = clip.GetClipProperty("Gamma")
        except Exception:
            pass

        already_709 = (
            current_color_space and "709" in str(current_color_space)
            and current_gamma and "709" in str(current_gamma)
        )
        if already_709:
            print(f"  [skip]   {name}: already Rec.709 ({current_color_space} / {current_gamma})")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  [would fix] {name}: {current_color_space} / {current_gamma} -> Rec.709")
            continue

        try:
            # Ensure per-clip RAW settings (rather than the project default)
            # are what actually gets applied to this clip.
            clip.SetClipProperty("Decode Using", "Clip")
        except Exception:
            pass

        applied_cs = set_property_with_fallback(clip, "Color Space", color_space_candidates)
        applied_gamma = set_property_with_fallback(clip, "Gamma", gamma_candidates)

        if applied_cs and applied_gamma:
            print(f"  [fixed]  {name}: -> Color Space={applied_cs}, Gamma={applied_gamma}")
            updated += 1
        else:
            print(
                f"  [FAILED] {name}: could not confirm Rec.709 RAW decode "
                f"(Color Space={applied_cs}, Gamma={applied_gamma}). "
                f"Run with --debug to inspect this clip's property names/values."
            )
            failed += 1

    print("\nSummary:")
    print(f"  updated: {updated}")
    print(f"  already Rec.709: {skipped}")
    print(f"  failed:  {failed}")
    if failed:
        print(
            "\nSome clips could not be confirmed as fixed. RAW decode "
            "property names can vary by Resolve version -- run with --debug "
            "to dump the exact GetClipProperty() keys/values for a ZRAW clip "
            "and adjust REC709_COLOR_SPACE_CANDIDATES / "
            "REC709_GAMMA_CANDIDATES at the top of this script accordingly."
        )


if __name__ == "__main__":
    main()
