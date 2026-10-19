"""
Scratch runner — resize ONE task file from input/ to an aspect ratio you pick.

Same pipeline as engine.main (resize -> self_check -> JSON + PNG), but instead
of running every task at its fixed target_canvas, it asks which file to use
and what aspect ratio to resize to.

    python -m engine.temp_main                          # interactive prompts
    python -m engine.temp_main -f 2 -r 16:9             # file #2, 16:9
    python -m engine.temp_main -f chameleon_sample_level1_warmup -r 4:5 -w 1200

The canvas size is derived from the ratio plus one fixed side: --width or
--height if given, otherwise the longer side of the task's own target_canvas.
Outputs land in output/ as <task>_<W>x<H>_output.{json,png}, so the regular
main.py outputs are not overwritten.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from engine.main import ASSETS_DIR, INPUT_DIR, OUTPUT_DIR, render_to_png, self_check
from engine.resize import resize


def parse_ratio(text: str) -> float:
    """Accepts '16:9', '16/9', '16x9' or a plain number like '1.78'."""
    text = text.strip().lower()
    for sep in (":", "/", "x"):
        if sep in text:
            w, h = text.split(sep, 1)
            ratio = float(w) / float(h)
            break
    else:
        ratio = float(text)
    if ratio <= 0:
        raise ValueError("aspect ratio must be positive")
    return ratio


def canvas_for(ratio: float, default: dict, width: int | None, height: int | None) -> dict:
    if width and height:
        return {"width": width, "height": height}
    if width:
        return {"width": width, "height": max(1, round(width / ratio))}
    if height:
        return {"width": max(1, round(height * ratio)), "height": height}
    long_side = max(default["width"], default["height"])
    if ratio >= 1:
        return {"width": long_side, "height": max(1, round(long_side / ratio))}
    return {"width": max(1, round(long_side * ratio)), "height": long_side}


def pick_file(task_files: list[Path], choice: str | None) -> Path:
    if choice is None:
        print("Task files in input/:")
        for i, p in enumerate(task_files, 1):
            print(f"  {i}. {p.name}")
        choice = input("Pick a file [1]: ").strip() or "1"

    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(task_files):
            return task_files[idx - 1]
        raise SystemExit(f"No file #{idx} (have 1-{len(task_files)})")
    for p in task_files:
        if choice in (p.name, p.stem):
            return p
    raise SystemExit(f"No task file named '{choice}' in {INPUT_DIR}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-f", "--file", help="task file: index from the list, name, or stem")
    ap.add_argument("-r", "--ratio", help="target aspect ratio W:H, e.g. 16:9, 4:5, 1.91")
    ap.add_argument("-w", "--width", type=int, help="fix the target width (height follows the ratio)")
    ap.add_argument("-H", "--height", type=int, help="fix the target height (width follows the ratio)")
    args = ap.parse_args()

    task_files = sorted(INPUT_DIR.glob("*.json"))
    if not task_files:
        print(f"No task files found in {INPUT_DIR}", file=sys.stderr)
        return 1

    task_path = pick_file(task_files, args.file)
    task = json.loads(task_path.read_text())
    source, default_target = task["source"], task["target_canvas"]
    src_c = source["canvas"]
    print(f"\n{task_path.name}: source {src_c['width']}x{src_c['height']}, "
          f"task target {default_target['width']}x{default_target['height']}")

    ratio_text = args.ratio
    if ratio_text is None:
        default_ratio = f"{default_target['width']}:{default_target['height']}"
        ratio_text = input(f"Target aspect ratio W:H [{default_ratio}]: ").strip() or default_ratio
    try:
        ratio = parse_ratio(ratio_text)
    except (ValueError, ZeroDivisionError) as e:
        raise SystemExit(f"Bad aspect ratio '{ratio_text}': {e}")

    target_canvas = canvas_for(ratio, default_target, args.width, args.height)
    tw, th = target_canvas["width"], target_canvas["height"]
    print(f"Resizing to {tw}x{th} (ratio {tw / th:.3f})")

    t0 = time.time()
    output = resize(source, target_canvas)
    elapsed = time.time() - t0

    problems = self_check(source, output)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{task_path.stem}_{tw}x{th}"
    (OUTPUT_DIR / f"{stem}_output.json").write_text(json.dumps(output, indent=2))
    render_to_png(output, ASSETS_DIR, OUTPUT_DIR / f"{stem}_output.png")

    status = "OK" if not problems else f"{len(problems)} self-check warning(s)"
    print(f"[{task_path.name}] {status} in {elapsed:.2f}s "
          f"-> {stem}_output.json, {stem}_output.png")
    for p in problems:
        print(f"    - {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
