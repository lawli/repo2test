"""Case ID and parametric ID generation."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class IdSpec:
    kind: str  # int64 | string
    prefix: int | str | None = None


def allocate_case_id() -> tuple[str, str]:
    """Return (full_uuid_str, 12-char hex short)."""
    cid = uuid.uuid4()
    short = cid.hex[:12]
    return str(cid), short


def _digest_int(case_id_short: str, name: str, max_digits: int) -> int:
    h = hashlib.sha256(f"{case_id_short}:{name}".encode()).hexdigest()
    n: int = int(h, 16) % (10 ** max_digits)
    return n


def allocate_ids(spec: dict[str, IdSpec], case_id_short: str) -> dict[str, int | str]:
    """Generate per-case parametric IDs deterministic from `case_id_short`."""
    out: dict[str, int | str] = {}
    for name, ispec in spec.items():
        if ispec.kind == "int64":
            prefix_int = int(ispec.prefix) if ispec.prefix is not None else 0
            tail = _digest_int(case_id_short, name, max_digits=12)
            out[name] = int(f"{prefix_int}{tail:012d}")
        elif ispec.kind == "string":
            prefix_str = str(ispec.prefix or "")
            out[name] = f"{prefix_str}{case_id_short}"
        else:
            raise ValueError(f"unknown id kind: {ispec.kind}")
    return out
