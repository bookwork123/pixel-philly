"""Download the static data the live transit layer needs (viewer/transit.js).

- SEPTA's static GTFS (routes, route shapes, the El timetable), pinned to one
  release on github.com/septadev/GTFS. SEPTA publishes a new release every
  few weeks; to move to a newer one, change GTFS_RELEASE, run this script and
  then scripts/35_build_transit.py. The old zip stays (raw data is
  immutable). SEPTA's developer license covers this static data
  (https://www3.septa.org/developer/). Credit: SEPTA.
- OpenStreetMap railway tunnels inside the map, via Overpass. The viewer hides
  vehicles in tunnels: trolleys in the Market St subway, the El under Market
  St and Regional Rail in the Center City Commuter Connection. License: ODbL
  1.0; credit "© OpenStreetMap contributors" (already in the map credits).

The live vehicle positions themselves are not downloaded here; the Cloudflare
Worker (worker/septa-live.js) or scripts/38_live_feed.py fetch them every
15 s.

Writes data/raw/septa_gtfs_<release>.zip and data/raw/philly_rail_tunnels_osm.json.
Usage: uv run python scripts/04r_fetch_transit.py
"""

import hashlib
import json

from pixelphilly.projection import REPO_ROOT
from pixelphilly.sources import RAW, fetch_overpass, get_with_retry, record

GTFS_RELEASE = "v202611010"
GTFS_URL = f"https://github.com/septadev/GTFS/releases/download/{GTFS_RELEASE}/gtfs_public.zip"
# The accepted map's lon/lat extent (map.json "fit"), so the tunnels cover
# exactly the area the viewer can show
MAP_JSON = REPO_ROOT / "output" / "philly" / "tiles" / "v18" / "map.json"


def fetch_gtfs():
    name = f"septa_gtfs_{GTFS_RELEASE}.zip"
    out = RAW / name
    if out.exists():
        print(f"{name} already downloaded; raw files are immutable, skipping")
        return
    r = get_with_retry(GTFS_URL, timeout=300)
    out.write_bytes(r.content)
    record(name, {"description": f"SEPTA static GTFS, release {GTFS_RELEASE} (gtfs_public.zip, which holds "
                                 "google_bus.zip and google_rail.zip)",
                  "query_url": GTFS_URL, "bytes": len(r.content),
                  "sha256": hashlib.sha256(r.content).hexdigest(),
                  "license": "SEPTA developer license, https://www3.septa.org/developer/",
                  "credit": "SEPTA"})
    print(f"{name}: {len(r.content) / 1e6:.1f} MB")


def fetch_tunnels():
    if not MAP_JSON.exists():
        raise SystemExit(f"{MAP_JSON} is missing; the tunnel query uses the map's extent")
    w, s, e, n = json.loads(MAP_JSON.read_text())["fit"]["bounds_wgs84"]
    box = f"{s},{w},{n},{e}"
    query = ('[out:json][timeout:180];'
             f'way["railway"~"^(rail|subway|light_rail|tram)$"]["tunnel"]["tunnel"!="no"]({box});'
             'out tags geom;')
    fetch_overpass(query, [w, s, e, n], "philly_rail_tunnels_osm.json",
                   "OpenStreetMap railway tunnels (rail, subway, light rail, tram) inside the map, via Overpass")


if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    fetch_gtfs()
    fetch_tunnels()
