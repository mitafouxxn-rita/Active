"""CLI のスループットとピークメモリを計測する。

    python scripts/generate_sample.py -n 100000 -o /tmp/sample.csv
    python scripts/benchmark.py /tmp/sample.csv

ピークメモリは子プロセスの最大 RSS（ru_maxrss）で計測する。
"""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import tempfile
import time


def main() -> None:
    src = sys.argv[1]
    extra = sys.argv[2:]
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "out.csv")
        t0 = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", "corpnorm", "--log-level", "WARNING", "process", src,
             "-o", out, "-q", *extra],
            check=True,
        )  # fmt: skip
        elapsed = time.perf_counter() - t0
        rss_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
        with open(src, "rb") as fh:
            rows = sum(1 for _ in fh) - 1
        size_mb = os.path.getsize(src) / 1024 / 1024
    print(f"rows          : {rows:,}")
    print(f"input size    : {size_mb:.1f} MB")
    print(f"elapsed       : {elapsed:.2f} s")
    print(f"throughput    : {rows / elapsed:,.0f} rows/s")
    print(f"peak RSS      : {rss_mb:.1f} MB")


if __name__ == "__main__":
    main()
