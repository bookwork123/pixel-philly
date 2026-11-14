"""Draw the vehicle sprites for the live transit layer (viewer/transit.js).

Each vehicle is a box at its real size, projected with the map's own camera
(pixelphilly.projection.IsoCamera: the same bearing, 45° tilt and 0.293 m per
pixel as every map frame), so a bus has the map's angle and scale exactly.
Boxes are filled without antialiasing and outlined in dark pixels, like the
map's pixel art. They are drawn at 72 compass headings (every 5°); the
viewer works out each car's compass bearing on the ground and picks the
closest.

Sizes are approximate published dimensions (meters), rounded:
- bus: New Flyer 40 ft (12.2 x 2.6 x 3.2), SEPTA white with blue and red bands
- trolley: Kawasaki LRV (15.2 x 2.6 x 3.4), cream with red and blue bands
- el: M-4 car (16.8 x 2.8 x 3.7), stainless with a Market-Frankford blue band
- rail: Silverliner IV car (25.9 x 3.2 x 4.4), stainless with red and blue bands
Trains are drawn one car at a time: the viewer lays cars out in ground meters
along the track, each facing along the chord between its two trucks
(TRUCKS), so they bend through curves the way real cars do.

Output: output/philly/transit/sprites.png and sprites.json:
  {"ground_to_px": [[..], [..]], "kinds": {kind: {"cars": n, "length_m": .., "gap_m": ..,
   "trucks": [front, rear], "sprites": [{"bearing", "angle", "len_px", "x", "y", "w", "h", "ax", "ay"}]}}}
  ground_to_px: map px = matrix @ (east m, north m) at ground level, the
  camera's own projection (the viewer inverts it to measure in meters);
  trucks: where the car rests, as fractions of its length from the front;
  angle: screen direction of travel, radians (atan2 of map px dy, dx);
  len_px: the car's length on screen along that direction; (ax, ay): where
  the car's ground center sits inside its cell.

Usage: uv run python scripts/37_vehicle_sprites.py
"""

import json
import math

import numpy as np
from PIL import Image, ImageDraw

from pixelphilly.projection import REPO_ROOT, IsoCamera, load_config

OUT = REPO_ROOT / "output" / "philly" / "transit"
# 72 headings (5° apart): a vehicle is never more than 2.5° off its road. With
# 16, it could be 11° off, which looked slanted on the city's streets (Nov. 14)
HEADINGS = 72
PAD = 3
SHEET_WIDTH = 2048  # rows wrap here; a few browsers limit very wide images

VEHICLES = {
    #        length, width, height (m), cars in a train, colors
    "bus": dict(size=(12.2, 2.6, 3.2), cars=1, body=(236, 236, 232), roof=(208, 210, 210),
                bands=[(0.12, 0.22, (200, 16, 46)), (0.22, 0.34, (34, 84, 160))], window=(52, 62, 76)),
    "trolley": dict(size=(15.2, 2.6, 3.4), cars=1, body=(232, 226, 206), roof=(196, 196, 190),
                    bands=[(0.14, 0.24, (34, 84, 160)), (0.24, 0.32, (200, 16, 46))], window=(52, 62, 76)),
    "el": dict(size=(16.8, 2.8, 3.7), cars=6, body=(186, 192, 198), roof=(150, 156, 162),
               bands=[(0.30, 0.40, (0, 151, 214))], window=(44, 54, 66)),
    "rail": dict(size=(25.9, 3.2, 4.4), cars=4, body=(190, 194, 198), roof=(140, 146, 152),
                 bands=[(0.26, 0.31, (200, 16, 46)), (0.31, 0.36, (34, 84, 160))], window=(44, 54, 66)),
}
OUTLINE = (34, 30, 38)
# Truck (or axle) positions as fractions of length from the front, approximate:
# bus axles sit near the ends' overhangs; rail cars' trucks about 15% in
TRUCKS = {"bus": [0.2, 0.75], "trolley": [0.2, 0.8], "el": [0.15, 0.85], "rail": [0.15, 0.85]}


def shade(c, k):
    return tuple(max(0, min(255, int(round(v * k)))) for v in c)


def project(cam, pts):
    e, n, h = np.asarray(pts, float).T
    sx, sy = cam.world_to_screen(e, n, h)
    return np.column_stack([sx, sy])


def draw_vehicle(cam, kind, bearing_deg):
    """One sprite: (RGBA image, anchor (ax, ay), screen angle, screen length)."""
    v = VEHICLES[kind]
    L, W, H = v["size"]
    b = math.radians(bearing_deg)
    f = np.array([math.sin(b), math.cos(b), 0.0])   # forward (east, north, up)
    r = np.array([math.cos(b), -math.sin(b), 0.0])  # to the vehicle's right
    z = np.array([0.0, 0.0, 1.0])

    def corner(sf, sr, sz):
        return f * sf * L / 2 + r * sr * W / 2 + z * sz * H

    # Faces as (corner list, outward normal, base color, side?) ; sides get bands and windows
    faces = [
        ([corner(1, -1, 0), corner(1, 1, 0), corner(1, 1, 1), corner(1, -1, 1)], f, "end"),
        ([corner(-1, 1, 0), corner(-1, -1, 0), corner(-1, -1, 1), corner(-1, 1, 1)], -f, "end"),
        ([corner(-1, 1, 0), corner(1, 1, 0), corner(1, 1, 1), corner(-1, 1, 1)], r, "side"),
        ([corner(1, -1, 0), corner(-1, -1, 0), corner(-1, -1, 1), corner(1, -1, 1)], -r, "side"),
    ]
    forward_view = cam.basis[0]
    all_pts = project(cam, [corner(sf, sr, sz) for sf in (-1, 1) for sr in (-1, 1) for sz in (0, 1)])
    x0, y0 = np.floor(all_pts.min(axis=0)) - PAD
    x1, y1 = np.ceil(all_pts.max(axis=0)) + PAD
    w, h = int(x1 - x0) + 1, int(y1 - y0) + 1
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    off = np.array([x0, y0])

    def poly(pts3, color):
        p = project(cam, pts3) - off
        d.polygon([tuple(np.round(q)) for q in p], fill=color + (255,))

    # Lighting: brighter when a face turns toward the sun (from the south-southeast,
    # as in render/render.js), never darker than 70%
    sun = np.array([0.35, -0.8, 0.0])
    sun /= np.linalg.norm(sun)
    for pts3, normal, part in faces:
        if np.dot(normal, forward_view) >= 0:
            continue  # faces away from the camera
        k = 0.78 + 0.22 * max(0.0, float(np.dot(normal, sun)))
        a, bq, c, dq = pts3  # bottom-left, bottom-right, top-right, top-left (seen from outside)
        poly(pts3, shade(v["body"], k))

        def band(t0, t1, color, inset=0.0):
            lo0, lo1 = a + (dq - a) * t0, bq + (c - bq) * t0
            hi0, hi1 = a + (dq - a) * t1, bq + (c - bq) * t1
            if inset:
                lo0, lo1 = lo0 + (lo1 - lo0) * inset, lo1 + (lo0 - lo1) * inset
                hi0, hi1 = hi0 + (hi1 - hi0) * inset, hi1 + (hi0 - hi1) * inset
            poly([lo0, lo1, hi1, hi0], shade(color, k))

        for t0, t1, color in v["bands"]:
            band(t0, t1, color)
        # A window strip: most of the side, the upper part of the end (windshield)
        band(0.52, 0.86, v["window"], inset=0.06 if part == "side" else 0.12)
    top = [corner(-1, -1, 1), corner(1, -1, 1), corner(1, 1, 1), corner(-1, 1, 1)]
    poly(top, v["roof"])

    # One dark pixel of outline around the silhouette
    alpha = np.asarray(img)[..., 3] > 0
    grown = alpha.copy()
    grown[1:] |= alpha[:-1]
    grown[:-1] |= alpha[1:]
    grown[:, 1:] |= alpha[:, :-1]
    grown[:, :-1] |= alpha[:, 1:]
    arr = np.asarray(img).copy()
    edge = grown & ~alpha
    arr[edge] = OUTLINE + (255,)
    img = Image.fromarray(arr)

    anchor = project(cam, [np.zeros(3)])[0] - off
    fwd = project(cam, [f * L / 2])[0] - project(cam, [-f * L / 2])[0]
    return img, (float(anchor[0]), float(anchor[1])), math.atan2(fwd[1], fwd[0]), float(np.hypot(*fwd))


def build(cam):
    """All sprites on one sheet, rows wrapped at SHEET_WIDTH: (sheet image, sprites.json dict)."""
    ex = cam.world_to_screen(1.0, 0.0, 0.0)
    nx = cam.world_to_screen(0.0, 1.0, 0.0)
    meta = {"meters_per_pixel": cam.meters_per_pixel,
            "ground_to_px": [[float(ex[0]), float(nx[0])], [float(ex[1]), float(nx[1])]], "kinds": {}}
    drawn = []
    for kind in VEHICLES:
        for i in range(HEADINGS):
            bearing = i * 360 / HEADINGS
            drawn.append((kind, bearing, *draw_vehicle(cam, kind, bearing)))
    # Place left to right, wrapping rows
    x = y = row_h = 0
    places = []
    for kind, bearing, img, *_ in drawn:
        if x + img.width > SHEET_WIDTH:
            x, y, row_h = 0, y + row_h, 0
        places.append((x, y))
        x += img.width
        row_h = max(row_h, img.height)
    sheet = Image.new("RGBA", (SHEET_WIDTH, y + row_h), (0, 0, 0, 0))
    for kind in VEHICLES:
        v = VEHICLES[kind]
        meta["kinds"][kind] = {"cars": v["cars"], "length_m": v["size"][0], "gap_m": 1.0, "trucks": TRUCKS[kind],
                               "sprites": []}
    for (kind, bearing, img, (ax, ay), angle, length), (px, py) in zip(drawn, places):
        sheet.paste(img, (px, py))
        meta["kinds"][kind]["sprites"].append({"bearing": bearing, "angle": round(angle, 5), "len_px": round(length, 2),
                                               "x": px, "y": py, "w": img.width, "h": img.height,
                                               "ax": round(ax, 2), "ay": round(ay, 2)})
    return sheet, meta


def main():
    cam = IsoCamera.from_config(load_config("south_lidar"))
    sheet, meta = build(cam)
    OUT.mkdir(parents=True, exist_ok=True)
    sheet.save(OUT / "sprites.png", optimize=True)
    (OUT / "sprites.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"sprites.png {sheet.width} x {sheet.height}, {len(VEHICLES) * HEADINGS} sprites")


if __name__ == "__main__":
    main()
