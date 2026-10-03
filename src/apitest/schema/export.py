"""Emit JSON Schema artifact from Pydantic model."""

from __future__ import annotations

import json
from pathlib import Path

from apitest.schema.case_v1 import Case


def export_schema(out_path: Path | str) -> None:
    schema = Case.model_json_schema(by_alias=True)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(schema, indent=2, sort_keys=True))


def main() -> None:
    here = Path(__file__).resolve().parent.parent / "schemas" / "case.schema.v1.json"
    export_schema(here)
    print(f"wrote {here}")


if __name__ == "__main__":
    main()
