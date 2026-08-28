# Blender

## DaVinci Resolve scripts

`scripts/zraw_to_rec709.py` — corrects Z CAM ZRAW footage to Rec.709 inside
DaVinci Resolve Studio 20. It scans the current project's Media Pool for
Z CAM ZRAW clips and switches their RAW decode settings (Color Space /
Gamma) from the camera-native ZRAW colour space to Rec.709, so the debayered
image is Rec.709 straight off the sensor data rather than flat/log.

See the docstring at the top of the script for installation (Workspace >
Scripts) and usage (`--dry-run`, `--debug`, etc.).
