"""scripts/36_build_occlusion.py: the mask pyramid lines up with v18."""

import json

import numpy as np
import pytest
from PIL import Image

from pixelphilly.projection import REPO_ROOT, area_setup, load_config
from pixelphilly.regions import union_lattice

OUT = REPO_ROOT / "output" / "philly" / "overlays" / "v18"


@pytest.mark.skipif(not (OUT / "occlusion.json").exists(), reason="occlusion pyramid not built on this machine")
def test_full_resolution_tiles_match_the_mask_frames():
    info = json.loads((OUT / "occlusion.json").read_text())
    frozen = json.loads((REPO_ROOT / "output" / "philly" / "FROZEN_v18.json").read_text())
    names = frozen["regions_in_precedence_order"]
    u = union_lattice([(n, area_setup(load_config(n))[2]) for n in names])
    top = max(info["levels"])
    rng = np.random.default_rng(1)
    cells = list(u["cells"].items())
    checked = 0
    for i in rng.choice(len(cells), 6, replace=False):
        (c, r), (area, frame) = cells[i]
        src = np.asarray(Image.open(REPO_ROOT / "output" / area / "masks" / f"{frame.name}.png").convert("L")) > 127
        if not src.any():
            continue
        size = src.shape[0]
        got = np.zeros_like(src)
        per = size // info["tile_size"]
        for dy in range(per):
            for dx in range(per):
                t = Image.open(OUT / "occlusion_files" / str(top) / f"{c * per + dx}_{r * per + dy}.png").convert("L")
                got[dy * 512:(dy + 1) * 512, dx * 512:(dx + 1) * 512] = np.asarray(t) > 127
        assert (got == src).all(), (area, frame.name)
        checked += 1
    assert checked >= 3
