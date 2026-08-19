import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.demo import demo_runtime

for scenario in ["happy", "stale", "false-success", "replay"]:
    print("\n===", scenario.upper(), "===")
    print(json.dumps(demo_runtime.run(scenario), indent=2))
