"""
Dev convenience script only - not part of the official CLI (assess/preprocess).
Runs quick assessment on every geojson file in the data directory and prints JSON.

Run (from the python/ directory):
    python -m scripts.dev_check

Or as a PyCharm Run Configuration:
    Module name: scripts.dev_check
    Working directory: <project>/python
"""
import json
from dataclasses import asdict
from pathlib import Path

from src.assessment.quick_assessment import run

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def main() -> None:
    geojson_files = sorted(DATA_DIR.glob("*.geojson"))
    if not geojson_files:
        print(f"No geojson files found in {DATA_DIR}")
        return

    for file_path in geojson_files:
        print(f"Processing: {file_path.resolve()}")
        result_dict = asdict(run(str(file_path)))
        print(json.dumps(result_dict, indent=4, ensure_ascii=False))
        print()


if __name__ == "__main__":
    main()
