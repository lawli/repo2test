"""Selector → pytest node-id translation."""

from __future__ import annotations

from pathlib import Path


class SelectorError(ValueError):
    pass


class AmbiguousSelector(SelectorError):
    pass


_HELPER_DIRS = {"_shared", "_helpers"}


def is_case_file(path: Path) -> bool:
    """A case is a `case_*.yaml` file outside `_shared`/`_helpers` dirs. Other
    YAML under tests/ (redis seeds, notes) is never collected as a case."""
    return (
        path.suffix == ".yaml"
        and path.name.startswith("case_")
        and not (_HELPER_DIRS & set(path.parts))
    )


def unguarded_python(tests_root: Path) -> list[Path]:
    """Python that apitest never runs: any `conftest.py`, and any other `.py` outside
    `_shared`/`_helpers` dirs (such as a package `__init__.py`). Helpers are loaded by
    file path and need neither."""
    return sorted(
        p
        for p in tests_root.rglob("*.py")
        if p.name == "conftest.py" or not (_HELPER_DIRS & set(p.relative_to(tests_root).parts))
    )


def resolve_selector(selector: str, *, tests_root: Path) -> list[str]:
    """Translate a path-prefix selector to one or more pytest node IDs.

    Selector forms:
      - "example-payment"                                          → all yaml under that dir
      - "example-payment/create_order"                             → subtree
      - "example-payment/create_order/happy_path"                  → subtree
      - "example-payment/create_order/happy_path/case_001"         → exact case (prefix-match name)
    """
    parts = selector.split("/")
    prefix = tests_root.joinpath(*parts)

    yamls: list[Path]
    if prefix.is_dir():
        yamls = sorted(p for p in prefix.rglob("*.yaml") if is_case_file(p))
    else:
        parent = tests_root.joinpath(*parts[:-1])
        if not parent.is_dir():
            raise SelectorError(f"selector path not found: {selector}")
        case_prefix = parts[-1]
        candidates = sorted(
            p for p in parent.glob(f"{case_prefix}*.yaml") if p.is_file() and is_case_file(p)
        )
        if len(candidates) == 0:
            raise SelectorError(f"no case matches {selector!r}")
        if len(candidates) > 1:
            raise AmbiguousSelector(
                f"selector {selector!r} matches {len(candidates)} files: "
                + ", ".join(p.name for p in candidates)
            )
        yamls = candidates

    if not yamls:
        raise SelectorError(f"no yaml cases under {selector!r}")
    return [f"{p}::{p.stem}" for p in yamls]
