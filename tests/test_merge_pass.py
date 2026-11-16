"""pixelphilly.merge_pass: merging a redraw pass into the base run
(scripts/39_merge_pass.py), on a fake output/ tree. Frame files are plain
bytes: the merge only copies and checksums them."""

import json
import os
import time

import pytest

from pixelphilly.merge_pass import (RECORD, MergeError, apply_merge, neighbors_holding, plan_merge, sha256_file,
                                    stale_after_repaint)

BASE = "qwen_joint_light8_fac"
PASS = f"{BASE}_bridges"


def put(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def age(path, seconds=100):
    """Make a file look `seconds` older, so later writes are newer."""
    t = time.time() - seconds
    os.utime(path, (t, t))


@pytest.fixture
def tree(tmp_path):
    out = tmp_path / "output"
    rd = out / "south" / "redraws"
    for n in ("f1", "f2", "f3"):
        put(rd / BASE / f"{n}.png", f"base {n}".encode())
        age(put(out / "south" / "postprocessed" / BASE / "water" / f"{n}.png", f"painted {n}".encode()))
        age(rd / BASE / f"{n}.png", 200)
    put(rd / PASS / "f1.png", b"new f1")             # redrawn
    put(rd / PASS / "f2.png", b"base f2")            # came out identical
    # north holds a copy of south's f1 (layout with one folder per owner)
    nb = out / "north" / "postprocessed" / BASE / "water_band"
    put(nb / "report.json", json.dumps({"owners": ["south"]}).encode())
    age(put(nb / "south" / "f1.png", b"north's copy of f1"))
    # west: flat layout, one known area
    wb = out / "west" / "postprocessed" / BASE / "water_band"
    put(wb / "report.json", json.dumps({"known_area": "south"}).encode())
    age(put(wb / "f1.png", b"west's copy of f1"))
    # east holds only f3, which the pass doesn't touch
    eb = out / "east" / "postprocessed" / BASE / "water_band"
    put(eb / "report.json", json.dumps({"owners": ["south"]}).encode())
    put(eb / "south" / "f3.png", b"east's copy of f3")
    return tmp_path


def test_plan_lists_changed_frames_and_every_area_holding_them(tree):
    plan = plan_merge(tree, "south", BASE, PASS)
    assert plan.frames == ["f1"]
    assert plan.unchanged == ["f2"]
    assert plan.suffix == "bridges"
    assert plan.backup_dir.name == f"{BASE}_pre_bridges"
    assert plan.neighbors == ["north", "west"], "east holds only frames the pass didn't change"
    assert not plan.backup_dir.exists(), "planning changes nothing"


def test_merge_backs_up_replaces_and_records(tree):
    rd = tree / "output" / "south" / "redraws"
    original = sha256_file(rd / BASE / "f1.png")
    record = apply_merge(plan_merge(tree, "south", BASE, PASS))
    assert (rd / f"{BASE}_pre_bridges" / "f1.png").read_bytes() == b"base f1"
    assert (rd / BASE / "f1.png").read_bytes() == b"new f1"
    assert (rd / BASE / "f2.png").read_bytes() == b"base f2"
    assert not (rd / f"{BASE}_pre_bridges" / "f2.png").exists(), "unchanged frames aren't backed up"
    saved = json.loads((rd / PASS / RECORD).read_text())
    assert saved == record
    assert saved["complete"] is True
    assert saved["frames"]["f1"] == {"before": original, "after": sha256_file(rd / PASS / "f1.png")}
    assert saved["repaint"] == ["south", "north", "west"]


def test_merged_frames_need_a_repaint_until_every_area_has_run(tree):
    """The pass was collected long before the area's last repaint, but the
    merged drawing must still count as newer (regions.stale_known)."""
    rd = tree / "output" / "south" / "redraws"
    age(rd / PASS / "f1.png", 10_000)
    plan = plan_merge(tree, "south", BASE, PASS)
    apply_merge(plan)
    stale = stale_after_repaint(tree, "south", BASE, plan.frames, plan.neighbors)
    assert stale == ["south/water/f1", "north/water_band/south/f1", "west/water_band/south/f1"]
    out = tree / "output"
    for p in (out / "south" / "postprocessed" / BASE / "water" / "f1.png",
              out / "north" / "postprocessed" / BASE / "water_band" / "south" / "f1.png"):
        p.write_bytes(b"repainted")
    assert stale_after_repaint(tree, "south", BASE, plan.frames, plan.neighbors) == ["west/water_band/south/f1"]
    (out / "west" / "postprocessed" / BASE / "water_band" / "f1.png").write_bytes(b"repainted")
    assert stale_after_repaint(tree, "south", BASE, plan.frames, plan.neighbors) == []


def test_a_pass_is_merged_only_once(tree):
    apply_merge(plan_merge(tree, "south", BASE, PASS))
    with pytest.raises(MergeError, match="already merged"):
        plan_merge(tree, "south", BASE, PASS)


def test_refuses_a_pass_still_drawing(tree):
    put(tree / "output" / "south" / "joint" / PASS / "call.json", b"{}")
    with pytest.raises(MergeError, match="still drawing"):
        plan_merge(tree, "south", BASE, PASS)
    put(tree / "output" / "south" / "joint" / PASS / "result.json", b"{}")
    assert plan_merge(tree, "south", BASE, PASS).frames == ["f1"]


def test_refuses_frames_the_base_never_drew(tree):
    put(tree / "output" / "south" / "redraws" / PASS / "f9.png", b"new frame")
    with pytest.raises(MergeError, match="no base frame"):
        plan_merge(tree, "south", BASE, PASS)
    assert plan_merge(tree, "south", BASE, PASS, only=["f1"]).frames == ["f1"]


def test_never_overwrites_a_backup(tree):
    backup = put(tree / "output" / "south" / "redraws" / f"{BASE}_pre_bridges" / "f1.png", b"older backup")
    with pytest.raises(MergeError, match="never overwritten"):
        plan_merge(tree, "south", BASE, PASS)
    assert backup.read_bytes() == b"older backup"
    assert plan_merge(tree, "south", BASE, PASS, suffix="bridges2").backup_dir.name == f"{BASE}_pre_bridges2"


def test_refuses_missing_folders_and_unknown_frames(tree):
    with pytest.raises(MergeError, match="no pass"):
        plan_merge(tree, "south", BASE, f"{BASE}_nothing")
    with pytest.raises(MergeError, match="no base run"):
        plan_merge(tree, "north", BASE, PASS)
    with pytest.raises(MergeError, match="not in the pass"):
        plan_merge(tree, "south", BASE, PASS, only=["f3"])
    with pytest.raises(MergeError, match="same folder"):
        plan_merge(tree, "south", BASE, BASE)


def test_neighbors_ignore_the_area_itself(tree):
    nb = tree / "output" / "south" / "postprocessed" / BASE / "water_band"
    put(nb / "report.json", json.dumps({"owners": ["south"]}).encode())
    put(nb / "south" / "f1.png", b"own band")
    assert neighbors_holding(tree, "south", BASE, ["f1"]) == ["north", "west"]
