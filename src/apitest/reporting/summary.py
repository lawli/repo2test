"""summary.json writer."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def write_summary(
    path: Path | str,
    cases: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    counts = Counter(c["outcome"] for c in cases)
    payload = {
        "totals": {
            "pass": counts.get("PASS", 0),
            "fail": counts.get("FAIL", 0),
            "error": counts.get("ERROR", 0),
            "blocked": counts.get("BLOCKED", 0),
            "xfail": counts.get("XFAIL", 0),
            "xpass": counts.get("XPASS", 0),
            # Subset of "fail": the unconfirmed expectation was not met.
            "pending_fail": sum(
                c["outcome"] == "FAIL" and c.get("expectation") == "pending" for c in cases
            ),
            "cleanup_failures": sum(len(c.get("teardown_errors", [])) for c in cases),
        },
        "cases": cases,
        "run": metadata or {},
        "complete": not counts.get("BLOCKED", 0)
        and len(cases) == (metadata or {}).get("collected_cases", len(cases)),
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))
