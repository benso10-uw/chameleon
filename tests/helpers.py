import json
from pathlib import Path

INPUT_DIR = Path(__file__).resolve().parent.parent / "input"


def load_task(level: str) -> dict:
    """Load a sample task by suffix, e.g. 'level2_core'."""
    return json.loads((INPUT_DIR / f"chameleon_sample_{level}.json").read_text())
