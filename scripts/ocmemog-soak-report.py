#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="input", default=str(REPO_ROOT / "reports" / "load" / "soak-latest.jsonl"))
    parser.add_argument("--out", default=str(REPO_ROOT / "reports" / "load" / "soak-report.html"))
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"missing {input_path}")

    rows = []
    for line in input_path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue

    if not rows:
        raise SystemExit("no data")

    def row_html(r):
        return (
            f"<tr><td>{r.get('timestamp')}</td>"
            f"<td>{r.get('mode')}</td>"
            f"<td>{r.get('concurrency')}</td>"
            f"<td>{r.get('requests')}</td>"
            f"<td>{r.get('errors')}</td>"
            f"<td>{r.get('avg_ms')}</td>"
            f"<td>{r.get('p95_ms')}</td></tr>"
        )

    html = """<html><head><title>ocmemog soak report</title>
    <style>table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px}</style>
    </head><body>
    <h2>ocmemog soak report</h2>
    <table>
      <tr><th>time</th><th>mode</th><th>concurrency</th><th>requests</th><th>errors</th><th>avg_ms</th><th>p95_ms</th></tr>
    """
    html += "\n".join(row_html(r) for r in rows)
    html += "</table></body></html>"

    Path(args.out).write_text(html, encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
