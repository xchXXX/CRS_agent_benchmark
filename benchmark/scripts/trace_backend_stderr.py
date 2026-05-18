from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trace appended backend stderr lines with local timestamps.")
    parser.add_argument("--log", required=True, help="Path to the backend stderr log file.")
    parser.add_argument("--out", required=True, help="Path to the temporary trace output file.")
    parser.add_argument("--duration-seconds", type=float, default=240.0, help="Max trace duration in seconds.")
    parser.add_argument("--poll-interval-ms", type=int, default=50, help="Polling interval in milliseconds.")
    parser.add_argument(
        "--start-at-end",
        action="store_true",
        help="Start tracing from the current file end instead of the beginning.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log_path = Path(args.log).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not log_path.exists():
        print(f"log file not found: {log_path}", file=sys.stderr)
        return 1

    poll_interval = max(args.poll_interval_ms, 10) / 1000.0
    deadline = time.monotonic() + max(args.duration_seconds, 1.0)

    with log_path.open("r", encoding="utf-8", errors="replace") as source, out_path.open(
        "w",
        encoding="utf-8",
    ) as sink:
        if args.start_at_end:
            source.seek(0, 2)

        sink.write(f"# trace_start epoch={time.time():.6f} log={log_path}\n")
        sink.flush()

        while time.monotonic() < deadline:
            line = source.readline()
            if line:
                now = time.time()
                sink.write(f"{now:.6f}\t{line}")
                if not line.endswith("\n"):
                    sink.write("\n")
                sink.flush()
                continue
            time.sleep(poll_interval)

        sink.write(f"# trace_end epoch={time.time():.6f}\n")
        sink.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
