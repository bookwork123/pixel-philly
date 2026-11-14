"""Build the building-occlusion pyramid the live transit layer uses to hide
vehicles behind buildings (viewer/transit.js).

Every frame was also rendered in "mask" mode (scripts/06_render.py --mode
mask): buildings and bridge cables white, everything else black, with the
map's own camera. Vehicles run at street level, so wherever a building pixel
covers the spot a vehicle is on, that building is in front of it: the viewer
draws the vehicles, then erases them wherever this mask is white.

The masks are joined exactly as the v18 illustration was (the same 12
regions in the same precedence order, from output/philly/FROZEN_v18.json),
so a mask pixel lines up with the map pixel it describes. The result goes in
its own folder, output/philly/overlays/v18/; the frozen v18 tiles are not
touched.

Output: output/philly/overlays/v18/occlusion.dzi + occlusion_files/<level>/<col>_<row>.png
  1-bit PNG, 512 px tiles. Only the top KEEP_LEVELS levels are kept: below
  that, vehicles are drawn as dots, which aren't hidden.

Usage: uv run python scripts/36_build_occlusion.py [--dry-run]
"""

import argparse
import importlib.util
import json
import shutil
import time

import pyvips

from pixelphilly.projection import REPO_ROOT, area_setup, load_config
from pixelphilly.regions import union_lattice

_spec = importlib.util.spec_from_file_location("build_tiles", REPO_ROOT / "scripts" / "16_build_tiles.py")
build_tiles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_tiles)

FROZEN = REPO_ROOT / "output" / "philly" / "FROZEN_v18.json"
MAP_JSON = REPO_ROOT / "output" / "philly" / "tiles" / "v18" / "map.json"
OUT = REPO_ROOT / "output" / "philly" / "overlays" / "v18"
KEEP_LEVELS = 3  # full, 1/2 and 1/4 resolution


def mask_path(area, frame):
    return REPO_ROOT / "output" / area / "masks" / f"{frame.name}.png"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    frozen = json.loads(FROZEN.read_text())
    meta = json.loads(MAP_JSON.read_text())
    names = frozen["regions_in_precedence_order"]
    cfgs = [load_config(n) for n in names]
    u = union_lattice([(c["name"], area_setup(c)[2]) for c in cfgs])
    size = cfgs[0]["camera"]["frame_px"]
    width, height = u["cols"] * size, u["rows"] * size
    # The mask must sit on exactly the v18 canvas
    if [u["x0"], u["y0"]] != meta["lattice_origin_px"] or (width, height) != (meta["width"], meta["height"]):
        raise SystemExit(f"union {width}x{height} at ({u['x0']}, {u['y0']}) doesn't match v18 "
                         f"{meta['width']}x{meta['height']} at {meta['lattice_origin_px']}")
    if u["counts"] != frozen["cells_per_region"]:
        raise SystemExit(f"cells per region {u['counts']} differ from v18's {frozen['cells_per_region']}")
    missing = [(a, f.name) for a, f in u["cells"].values() if not mask_path(a, f).exists()]
    print(f"union {width:,} x {height:,} px, {len(u['cells'])} cells; masks missing: {len(missing)}")
    if missing:
        raise SystemExit(f"missing masks, e.g. {missing[:3]}; render them with 06_render.py --mode mask")
    if args.dry_run:
        return
    if (OUT / "occlusion.dzi").exists():
        raise SystemExit(f"{OUT} already built")
    OUT.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    # Mask frames are antialiased on their edges; 50% is the boundary the
    # alignment check uses too. Cells no area provides stay black (no building).
    img = build_tiles.union_layer(u, size, mask_path).extract_band(0)
    binary = (img > 127).ifthenelse(255, 0).cast("uchar")
    binary.dzsave(str(OUT / "occlusion"), tile_size=512, overlap=0, layout="dz",
                  suffix=".png[bitdepth=1,compression=9]")
    levels = sorted(int(p.name) for p in (OUT / "occlusion_files").iterdir() if p.name.isdigit())
    top = levels[-1]
    for lv in levels:
        if lv <= top - KEEP_LEVELS:
            shutil.rmtree(OUT / "occlusion_files" / str(lv))
    files = list((OUT / "occlusion_files").rglob("*.png"))
    info = {"map_version": "v18", "dzi": "occlusion.dzi", "levels": list(range(top - KEEP_LEVELS + 1, top + 1)),
            "tile_size": 512, "width": width, "height": height,
            "meaning": "white = a building (or bridge cable) is drawn here; vehicles under white are hidden",
            "source": "output/<region>/masks/<frame>.png (06_render.py --mode mask), joined in v18's region order"}
    (OUT / "occlusion.json").write_text(json.dumps(info, indent=1) + "\n")
    print(f"occlusion: levels {info['levels']}, {len(files)} tiles, "
          f"{sum(f.stat().st_size for f in files) / 1e6:.0f} MB, {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
