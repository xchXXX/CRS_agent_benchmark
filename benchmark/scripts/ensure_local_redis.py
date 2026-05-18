from __future__ import annotations

import json
from pathlib import Path
import sys


CURRENT_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = CURRENT_DIR.parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))


from doc_search_bench.runtime_prep import ensure_local_redis_running


def main() -> int:
    result = ensure_local_redis_running()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
