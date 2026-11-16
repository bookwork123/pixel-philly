"""Merge a redraw pass into an area's base run (scripts/39_merge_pass.py).

A fix pass (15_joint_sample.py --redraw ... --name <pass>) writes only the
frames it redrew, to output/<area>/redraws/<pass>/. Until Nov. 2026 merging
those into the base run was a manual copy (README "Fix passes"); this does
the file side of it:

1. back up the base frames being replaced to
   redraws/<base>_pre_<suffix>/ (never over an existing backup);
2. copy the pass's frames over the base run's;
3. record what was replaced, with checksums, in redraws/<pass>/merged.json.

The repaints that must follow (the area itself, then every area holding a
copy of those frames in its water_band/) are found by neighbors_holding()
and checked afterward by stale_after_repaint(). Everything here is file
I/O only (no model, no repaint) and tested on CPU (tests/test_merge_pass.py).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .regions import _band_files

RECORD = "merged.json"


class MergeError(Exception):
    pass


def sha256_file(path, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


@dataclass
class MergePlan:
    area: str
    base: str
    pass_name: str
    suffix: str
    base_dir: Path
    pass_dir: Path
    backup_dir: Path
    frames: list[str]                  # frames to copy in (differ from the base)
    unchanged: list[str] = field(default_factory=list)  # identical to the base: skipped
    neighbors: list[str] = field(default_factory=list)  # areas to repaint after this one


def redraws_dir(root, area) -> Path:
    return Path(root) / "output" / area / "redraws"


def plan_merge(root, area, base, pass_name, suffix=None, only=None) -> MergePlan:
    """Check a pass can be merged and list what would change. Raises
    MergeError (and changes nothing) when it can't.

    suffix names the backup folder (<base>_pre_<suffix>); default: the
    pass name with the "<base>_" prefix removed (qwen_joint_light8_fac_bridges
    -> bridges). only: merge just these frames of the pass."""
    rd = redraws_dir(root, area)
    base_dir, pass_dir = rd / base, rd / pass_name
    if pass_name == base:
        raise MergeError("the pass and the base run are the same folder")
    if not base_dir.is_dir():
        raise MergeError(f"no base run at {base_dir}")
    if not pass_dir.is_dir():
        raise MergeError(f"no pass at {pass_dir}")
    if (pass_dir / RECORD).exists():
        raise MergeError(f"{pass_name} was already merged ({pass_dir / RECORD})")
    joint = Path(root) / "output" / area / "joint" / pass_name
    if (joint / "call.json").exists() and not (joint / "result.json").exists():
        raise MergeError(f"{area}/{pass_name} is still drawing: collect it first (15_joint_sample.py --collect)")
    if suffix is None:
        suffix = pass_name[len(base) + 1:] if pass_name.startswith(base + "_") else pass_name
    if not suffix:
        raise MergeError("empty backup suffix: pass --suffix")
    backup_dir = rd / f"{base}_pre_{suffix}"

    available = sorted(p.stem for p in pass_dir.glob("*.png"))
    if only is not None:
        missing = sorted(set(only) - set(available))
        if missing:
            raise MergeError(f"frames not in the pass: {missing}")
        available = sorted(set(only))
    if not available:
        raise MergeError(f"no frames in {pass_dir}")
    not_in_base = [n for n in available if not (base_dir / f"{n}.png").exists()]
    if not_in_base:
        raise MergeError(f"{len(not_in_base)} pass frames have no base frame to replace, e.g. {not_in_base[:3]}; "
                         "a redraw pass only replaces frames the base run drew")
    frames, unchanged = [], []
    for n in available:
        same = sha256_file(pass_dir / f"{n}.png") == sha256_file(base_dir / f"{n}.png")
        (unchanged if same else frames).append(n)
    clash = [n for n in frames if (backup_dir / f"{n}.png").exists()]
    if clash:
        raise MergeError(f"backup {backup_dir.name} already holds {len(clash)} of these frames, e.g. {clash[:3]}; "
                         "backups are never overwritten: pick another --suffix")
    return MergePlan(area, base, pass_name, suffix, base_dir, pass_dir, backup_dir, frames, unchanged,
                     neighbors_holding(root, area, base, frames))


def neighbors_holding(root, area, prompt, frames) -> list[str]:
    """Other areas whose water_band/ holds a re-repainted copy of any of
    `area`'s `frames` (regions._band_files layouts). Those copies replace
    `area`'s pixels in the merged map (regions.cell_sources), so each of
    these areas must be repainted after `area` is."""
    out = []
    for band in sorted((Path(root) / "output").glob(f"*/postprocessed/{prompt}/water_band")):
        holder = band.parts[-4]
        if holder == area:
            continue
        files = _band_files(band)
        if area in files and any((files[area] / f"{n}.png").exists() for n in frames):
            out.append(holder)
    return out


def apply_merge(plan: MergePlan) -> dict:
    """Back up, copy in and write the record. Returns the record.

    The base frames are copied to the backup with their timestamps
    (copy2). The pass frames are copied in with a FRESH modification time
    (copyfile): regions.stale_known flags a frame as needing a repaint when
    its drawing is newer than its repainted PNG, and a pass collected before
    the area's last repaint would otherwise look already repainted."""
    record = {
        "area": plan.area, "base": plan.base, "pass": plan.pass_name,
        "backup": plan.backup_dir.name,
        "merged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frames": {}, "unchanged": plan.unchanged, "repaint": [plan.area, *plan.neighbors],
    }
    if not plan.frames:
        return record
    # Every backup is made and checked before any base frame is replaced
    plan.backup_dir.mkdir(parents=True, exist_ok=True)
    before = {}
    for n in plan.frames:
        dst = plan.base_dir / f"{n}.png"
        before[n] = sha256_file(dst)
        shutil.copy2(dst, plan.backup_dir / f"{n}.png")
        if sha256_file(plan.backup_dir / f"{n}.png") != before[n]:
            raise MergeError(f"backup of {n} doesn't match the original; nothing was replaced")
    # The record is written even if copying stops partway, so it always
    # says which base frames were replaced
    record["complete"] = False
    try:
        for n in plan.frames:
            dst = plan.base_dir / f"{n}.png"
            shutil.copyfile(plan.pass_dir / f"{n}.png", dst)
            record["frames"][n] = {"before": before[n], "after": sha256_file(dst)}
        record["complete"] = True
    finally:
        (plan.pass_dir / RECORD).write_text(json.dumps(record, indent=2) + "\n")
    return record


def stale_after_repaint(root, area, prompt, frames, neighbors) -> list[str]:
    """Repainted files still older than the merged drawings: `area`'s
    postprocessed/<prompt>/water/<frame>.png, and each neighbor's
    water_band copy of those frames. Empty once every repaint has run."""
    root = Path(root)
    out = []
    for n in frames:
        drawn = redraws_dir(root, area) / prompt / f"{n}.png"
        t = drawn.stat().st_mtime
        own = root / "output" / area / "postprocessed" / prompt / "water" / f"{n}.png"
        if not own.exists() or own.stat().st_mtime < t:
            out.append(f"{area}/water/{n}")
        for holder in neighbors:
            files = _band_files(root / "output" / holder / "postprocessed" / prompt / "water_band")
            copy = files.get(area, Path("/nonexistent")) / f"{n}.png"
            if copy.exists() and copy.stat().st_mtime < t:
                out.append(f"{holder}/water_band/{area}/{n}")
    return out
