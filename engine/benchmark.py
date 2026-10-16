"""Compare reasoning settings on one task. Outputs are saved in workbench history."""

import argparse
import json
import os
from pathlib import Path

from engine.model.model import load_env_file
from engine.workbench import ROOT, Run, make_model, validate_task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    parser.add_argument("--reasoning", nargs="+", choices=("off", "minimal", "low", "auto", "high"), default=["minimal", "low"])
    parser.add_argument("--attempts", type=int, choices=(1, 2, 3), default=2)
    args = parser.parse_args()
    load_env_file(ROOT / ".env")
    task = validate_task(json.loads(args.task.read_text(encoding="utf-8-sig")))
    for reasoning in args.reasoning:
        config = {"provider": "gemini", "model": args.model, "reasoning": reasoning,
                  "attempts": args.attempts, "refine": True}
        run = Run(task, args.task.stem, make_model(config), config)
        run.work()
        data = run.snapshot()
        calls = [s for s in data["steps"] if s["kind"] == "llm_call"]
        result = next((s for s in reversed(data["steps"]) if s["kind"] == "result"), {})
        print(json.dumps({"id": data["id"], "reasoning": reasoning, "seconds": data["seconds"],
                          "state": data["state"], "winner": result.get("winner"),
                          "report": result.get("report"), "calls": len(calls),
                          "failures": [s.get("error") for s in calls if s["outcome"] != "usable"],
                          "usage": [s.get("usage") for s in calls]}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
