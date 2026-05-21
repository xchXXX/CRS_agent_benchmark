from __future__ import annotations

import sys
from pathlib import Path


BENCHMARK_DIR = Path(__file__).resolve().parents[2]
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from doc_search_bench.chat_export.render_first_attempt_review_html import *  # noqa: F401,F403


if __name__ == "__main__":
    from doc_search_bench.chat_export.render_first_attempt_review_html import main as _main

    _main()
