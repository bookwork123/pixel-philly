"""Merge a redraw pass into an area's base run, then repaint what it touched.

Replaces the manual steps in README "Fix passes" (pixelphilly.merge_pass):
1. back up the base frames being replaced to redraws/<base>_pre_<suffix>/;
2. copy the pass's frames over redraws/<base>/ and record it in
   redraws/<pass>/merged.json;
3. repaint the area: 10_repaint_water.py --prompt <base> --invented-water fix;
4. repaint every other area holding a copy of those frames in its
   water_band/ (found from the files, not from memory);
5. check that no repainted file is older than the merged drawings.

It refuses, changing nothing, when the pass is still drawing, was already
merged, has frames the base run lacks, or the backup folder already holds
those frames.

Usage:
  PP_AREA=south_lidar uv run python scripts/39_merge_pass.py --pass qwen_joint_light8_fac_v18rest --dry-run
  PP_AREA=south_lidar uv run python scripts/39_merge_pass.py --pass qwen_joint_light8_fac_v18rest
  ... --no-repaint      only steps 1-2; prints the repaint commands to run
  ... --check-only      only step 5 (after repainting by hand)
"""

import argparse
import json
import os
import subprocess
import sys

from pixelphilly.merge_pass import RECORD, MergeError, apply_merge, plan_merge, stale_after_repaint
from pixelphilly.projection import REPO_ROOT

BASE = "qwen_joint_light8_fac"


def repaint_command(area, base, invented_water):
    return [sys.executable, str(REPO_ROOT / "scripts" / "10_repaint_water.py"), "--prompt", base,
            "--invented-water", invented_water], {**os.environ, "PP_AREA": area}


def show(area, cmd):
    return f"PP_AREA={area} uv run python scripts/10_repaint_water.py " + " ".join(cmd[2:])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--area", default=os.environ.get("PP_AREA"), help="area (default: PP_AREA)")
    ap.add_argument("--pass", dest="pass_name", required=True, help="the redraw pass's run name")
    ap.add_argument("--base", default=BASE, help=f"the run to merge into (default {BASE})")
    ap.add_argument("--suffix", help="backup folder <base>_pre_<suffix> (default: the pass name minus '<base>_')")
    ap.add_argument("--frames", help="merge only these frames of the pass, comma list")
    ap.add_argument("--invented-water", choices=["off", "fix"], default="fix",
                    help="passed to 10_repaint_water.py (default fix, as for every v18 pass)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    ap.add_argument("--no-repaint", action="store_true", help="merge the files only; print the repaint commands")
    ap.add_argument("--check-only", action="store_true", help="only check that the repaints after a merge ran")
    args = ap.parse_args()
    if not args.area:
        sys.exit("no area: set PP_AREA or pass --area")
    if not (REPO_ROOT / "config" / f"{args.area}.json").exists():
        sys.exit(f"no config/{args.area}.json")

    pass_dir = REPO_ROOT / "output" / args.area / "redraws" / args.pass_name
    if args.check_only:
        if not (pass_dir / RECORD).exists():
            sys.exit(f"{args.pass_name} hasn't been merged (no {RECORD})")
        record = json.loads((pass_dir / RECORD).read_text())
        return check(args.area, record["base"], list(record["frames"]), record["repaint"][1:])

    only = [f.strip() for f in args.frames.split(",") if f.strip()] if args.frames else None
    try:
        plan = plan_merge(REPO_ROOT, args.area, args.base, args.pass_name, args.suffix, only)
    except MergeError as e:
        sys.exit(f"Stopped: {e}")

    print(f"{args.area}: merge {plan.pass_name} into {plan.base}")
    print(f"  {len(plan.frames)} frames to replace: {', '.join(plan.frames) or '(none)'}")
    if plan.unchanged:
        print(f"  {len(plan.unchanged)} identical to the base, skipped: {', '.join(plan.unchanged)}")
    print(f"  backup: redraws/{plan.backup_dir.name}/")
    print(f"  repaint, in order: {', '.join([plan.area, *plan.neighbors])}")
    commands = [(a, *repaint_command(a, plan.base, args.invented_water)) for a in [plan.area, *plan.neighbors]]
    if not plan.frames:
        print("Nothing to merge.")
        return
    if args.dry_run:
        for a, cmd, _ in commands:
            print(f"  would run: {show(a, cmd)}")
        print("Dry run: nothing changed.")
        return

    try:
        record = apply_merge(plan)
    except MergeError as e:
        sys.exit(f"Stopped: {e}")
    print(f"Merged {len(record['frames'])} frames; record in redraws/{plan.pass_name}/{RECORD}")

    if args.no_repaint:
        print("Now run, in this order:")
        for a, cmd, _ in commands:
            print(f"  {show(a, cmd)}")
        print(f"then: PP_AREA={plan.area} uv run python scripts/39_merge_pass.py --pass {plan.pass_name} --check-only")
        return
    for a, cmd, env in commands:
        print(f"Repainting {a}: {show(a, cmd)}", flush=True)
        if subprocess.run(cmd, env=env, cwd=REPO_ROOT).returncode != 0:
            rest = [show(b, c) for b, c, _ in commands[commands.index((a, cmd, env)):]]
            sys.exit(f"Repaint of {a} failed. The files are merged; once it's fixed, run:\n  " + "\n  ".join(rest))
    check(plan.area, plan.base, plan.frames, plan.neighbors)


def check(area, base, frames, neighbors):
    stale = stale_after_repaint(REPO_ROOT, area, base, frames, neighbors)
    if stale:
        sys.exit(f"{len(stale)} repainted files are older than the merged drawings, e.g. {stale[:3]}")
    print(f"All repaints are current ({len(frames)} frames; {area} and {len(neighbors)} neighboring areas).")


if __name__ == "__main__":
    main()
