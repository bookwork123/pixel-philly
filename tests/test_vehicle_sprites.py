"""scripts/37_vehicle_sprites.py: boxes drawn with the map's own camera."""

import importlib.util
import math

import numpy as np
import pytest

from pixelphilly.projection import REPO_ROOT, IsoCamera, load_config

_spec = importlib.util.spec_from_file_location("sprites", REPO_ROOT / "scripts" / "37_vehicle_sprites.py")
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)

CAM = IsoCamera.from_config(load_config("south_lidar"))


@pytest.mark.parametrize("bearing", [0, 90, 202.5])
def test_sprite_points_where_the_camera_puts_that_compass_direction(bearing):
    _, _, angle, length = sp.draw_vehicle(CAM, "bus", bearing)
    b = math.radians(bearing)
    sx, sy = CAM.world_to_screen(math.sin(b), math.cos(b), 0.0)
    assert angle == pytest.approx(math.atan2(sy, sx), abs=1e-9)
    # 12.2 m bus, foreshortened by the 45° tilt but never longer than true size
    assert 12.2 / CAM.meters_per_pixel * 0.7 < length <= 12.2 / CAM.meters_per_pixel + 1e-6


def test_sheet_has_every_kind_and_heading():
    sheet, meta = sp.build(CAM)
    assert set(meta["kinds"]) == {"bus", "trolley", "el", "rail"}
    for kind in meta["kinds"].values():
        assert len(kind["sprites"]) == sp.HEADINGS
        for s in kind["sprites"]:
            cell = np.asarray(sheet.crop((s["x"], s["y"], s["x"] + s["w"], s["y"] + s["h"])))
            assert cell[..., 3].any()  # something was drawn
            assert 0 <= s["ax"] < s["w"] and 0 <= s["ay"] < s["h"]
            # The ground center is covered by the vehicle (it sits on its own footprint)
            assert cell[int(round(s["ay"])), int(round(s["ax"])), 3] > 0


def test_ground_matrix_is_the_camera():
    _, meta = sp.build(CAM)
    A = np.array(meta["ground_to_px"])
    for e, n in [(1, 0), (0, 1), (3.5, -2)]:
        sx, sy = CAM.world_to_screen(e, n, 0.0)
        assert A @ [e, n] == pytest.approx([sx, sy], abs=1e-9)
    assert meta["kinds"]["el"]["trucks"] == [0.15, 0.85]
