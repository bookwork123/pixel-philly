"""Build the static files for the live transit layer (viewer/transit.js).

Input:  data/raw/septa_gtfs_<release>.zip, data/raw/philly_rail_tunnels_osm.json
        (scripts/04r_fetch_transit.py), data/raw/city_limits.geojson,
        output/philly/tiles/v18/map.json (the lon/lat -> pixel fit)
Output: output/philly/transit/<release>/
          routes.json          every drawn route: kind, SEPTA color, name, shape
                               file; Regional Rail line names; the city line in
                               map px (vehicles outside it aren't drawn)
          routes/<route>.json  that route's shapes in map px, with tunnel stretches
          el_schedule.json     the Market-Frankford El timetable (no live El data)

Each GTFS release gets its own folder, like map versions, so a CDN never
serves a mix of old and new files. The viewer reads the folder named in
viewer/transit.js (GTFS_RELEASE).

Usage: uv run python scripts/35_build_transit.py [--release v202611010]
"""

import argparse
import json
import time
from collections import defaultdict
from datetime import date

import numpy as np
from shapely.geometry import shape

from pixelphilly import transit as tr
from pixelphilly.projection import REPO_ROOT
from pixelphilly.sources import RAW
from pixelphilly.viewer_geo import lonlat_to_px

MAP_JSON = REPO_ROOT / "output" / "philly" / "tiles" / "v18" / "map.json"
OUT_ROOT = REPO_ROOT / "output" / "philly" / "transit"


def city_line_px(fit):
    geo = json.loads((RAW / "city_limits.geojson").read_text())
    poly = max((shape(f["geometry"]) for f in geo["features"]), key=lambda g: g.area)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    lon, lat = np.asarray(poly.exterior.coords).T
    x, y = lonlat_to_px(fit, lon, lat)
    from shapely.geometry import Polygon
    simple = Polygon(np.column_stack([x, y])).simplify(8.0)  # about 2 m: plenty for "is it in the city"
    return [int(round(v)) for v in np.asarray(simple.exterior.coords).ravel()]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", default="v202611010")
    args = ap.parse_args()
    t0 = time.time()

    meta = json.loads(MAP_JSON.read_text())
    fit = meta["fit"]
    clip = fit["bounds_wgs84"]
    feeds = tr.read_gtfs(RAW / f"septa_gtfs_{args.release}.zip")
    tunnels = tr.Tunnels(json.loads((RAW / "philly_rail_tunnels_osm.json").read_text()))
    out = OUT_ROOT / args.release
    if out.exists():
        raise SystemExit(f"{out} exists; each release is built once (delete it to rebuild)")
    (out / "routes").mkdir(parents=True)

    routes, rail_lines, el_shapes, el_alias = {}, {}, {}, {}
    for feed_name, feed in feeds.items():
        shapes = tr.shapes_by_id(feed["shapes"])
        used = defaultdict(set)
        for t in feed["trips"]:
            used[t["route_id"]].add(t["shape_id"])
        for r in feed["routes"]:
            rid = r["route_id"]
            kind = tr.route_kind(rid, r["route_type"])
            if kind is None or not used[rid]:
                continue
            pieces, seen = [], {}
            for sid in sorted(used[rid]):
                if sid not in shapes:
                    continue
                for i, piece in enumerate(tr.shape_pieces(*shapes[sid], fit, clip, kind, tunnels)):
                    key = tuple(piece["pts"])
                    if len(key) < 4:
                        continue
                    pid = f"{sid}" if i == 0 else f"{sid}.{i}"
                    if kind == "el" and i == 0:
                        # Every El shape is scheduled, but trips on a duplicate
                        # shape point at the copy that was kept
                        pts = np.asarray(piece["pts"], float).reshape(-1, 2)
                        el_shapes[sid] = (pts, tr.cumulative(pts))
                        el_alias[sid] = seen.get(key, pid)
                    if key in seen:
                        continue
                    seen[key] = pid
                    pieces.append({"id": pid, **piece})
            if not pieces:
                continue  # the route never enters the map
            (out / "routes" / f"{rid}.json").write_text(json.dumps({"route": rid, "shapes": pieces},
                                                                     separators=(",", ":")))
            routes[rid] = {"kind": kind, "color": "#" + (r.get("route_color") or "666666"),
                           "name": r.get("route_long_name") or r.get("route_short_name") or rid,
                           "short": r.get("route_short_name") or rid}
            if kind == "rail":
                # TrainView calls the line "Trenton"; GTFS calls it "Trenton Line"
                rail_lines[routes[rid]["name"].removesuffix(" Line")] = rid
            print(f"  {rid:>8} {kind:8} {len(pieces)} shapes")

    bus = feeds["bus"]
    sched = tr.el_schedule(bus, el_shapes, fit)
    for p in sched["patterns"]:
        p["shape"] = el_alias[p["shape"]]
    sched["services"] = tr.services_by_date(bus["calendar"], bus["calendar_dates"])
    # Only the service ids the El uses, so the file stays small
    el_services = {t[2] for t in sched["trips"]}
    # Days with no El service are left out, so the viewer falls back to a matching day instead of showing none
    sched["services"] = {d: kept for d, ids in sched["services"].items() if (kept := [s for s in ids if s in el_services])}
    (out / "el_schedule.json").write_text(json.dumps(sched, separators=(",", ":")))

    index = {"gtfs_release": args.release, "built": date.today().isoformat(), "map_version": meta["version"],
             "routes": routes, "rail_lines": rail_lines, "city": city_line_px(fit),
             "credit": "Vehicle positions and schedules: SEPTA. Tunnels: © OpenStreetMap contributors."}
    (out / "routes.json").write_text(json.dumps(index, separators=(",", ":")))

    sizes = sum(p.stat().st_size for p in out.rglob("*.json"))
    print(f"{len(routes)} routes ({sum(r['kind'] == 'bus' for r in routes.values())} bus), "
          f"{len(sched['patterns'])} El patterns, {len(sched['trips'])} El trips, "
          f"service dates {min(sched['services'])}-{max(sched['services'])}; "
          f"{sizes / 1e6:.2f} MB in {out} ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
