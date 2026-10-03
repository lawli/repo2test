"""Helper resolution + typed DI."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


class HelperError(RuntimeError):
    pass


class AmbiguousHelper(HelperError):
    pass


class HelperNotFound(HelperError):
    pass


_OVERRIDE_ATTR = "__apitest_override__"


def override(target: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Marks a helper as an explicit override of the named target dotted path."""
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(fn, _OVERRIDE_ATTR, target)
        return fn
    return deco


class HelperResolver:
    """Walks scope chain at collection time and resolves dotted paths to callables."""

    def __init__(self, tests_root: Path) -> None:
        self._tests_root = Path(tests_root)
        self._modules: dict[tuple[Path, str], ModuleType] = {}

    def _scope_chain(self, case_path: Path) -> list[Path]:
        """Most-specific to least-specific helper search dirs."""
        case_path = case_path.resolve()
        rel = case_path.relative_to(self._tests_root.resolve())
        # rel is e.g. example-payment/scen/suite/case_001.yaml
        parts = list(rel.parts)
        out: list[Path] = []
        # case_path's parent is the suite folder
        suite = case_path.parent
        out.append(suite / "_helpers")
        if len(parts) >= 3:
            scenario = self._tests_root.resolve() / parts[0] / parts[1]
            out.append(scenario / "_shared" / "helpers")
        if len(parts) >= 2:
            service = self._tests_root.resolve() / parts[0]
            out.append(service / "_shared" / "helpers")
        return out

    def resolve(self, dotted: str, *, case_path: Path) -> Callable[..., Any]:
        """`helpers.<module>.<func>` -> callable."""
        if not dotted.startswith("helpers."):
            raise HelperNotFound(f"dotted path must begin with 'helpers.': {dotted}")
        _, module_name, func_name = dotted.split(".", 2)

        candidates: list[tuple[Path, Callable[..., Any]]] = []
        for scope in self._scope_chain(case_path):
            f = scope / f"{module_name}.py"
            if f.exists():
                fn = self._load_func(f, func_name, dotted)
                candidates.append((f, fn))

        # Framework builtins
        try:
            mod = importlib.import_module(f"apitest.helpers.{module_name}")
            if hasattr(mod, func_name):
                fpath = Path(mod.__file__ or "")
                candidates.append((fpath, getattr(mod, func_name)))
        except (ImportError, AttributeError):
            pass

        if not candidates:
            raise HelperNotFound(f"helper not found: {dotted}")
        if len(candidates) == 1:
            return candidates[0][1]

        overrides = [
            (p, fn) for p, fn in candidates if getattr(fn, _OVERRIDE_ATTR, None) == dotted
        ]
        if len(overrides) == 1:
            return overrides[0][1]
        if len(overrides) > 1:
            paths = ", ".join(str(p) for p, _ in overrides)
            raise AmbiguousHelper(f"multiple @override for {dotted}: {paths}")

        paths = ", ".join(str(p) for p, _ in candidates)
        raise AmbiguousHelper(
            f"helper {dotted} defined in {len(candidates)} scopes ({paths}); "
            "annotate the local one with @override(<dotted>) to disambiguate"
        )

    def _load_module(self, file: Path) -> ModuleType:
        """Execute a helper file once; later resolves reuse the module (so
        module-level state such as a token cache persists) and it is
        registered in sys.modules so dataclasses/pickling/relative lookups work."""
        file = file.resolve()
        source = file.read_bytes()
        digest = hashlib.sha256(str(file).encode() + b"\0" + source).hexdigest()
        key = (file, digest)
        if key in self._modules:
            return self._modules[key]
        name = f"apitest_helpers.{file.stem}_{digest}"
        # The pytest plugin builds a resolver per case; reuse a module another
        # resolver already executed so helper state spans the whole run.
        existing = sys.modules.get(name)
        if existing is not None:
            self._modules[key] = existing
            return existing
        spec = importlib.util.spec_from_file_location(name, str(file))
        if spec is None or spec.loader is None:
            raise HelperError(f"cannot load {file}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            # Execute exactly the inspected source, without timestamp-based .pyc reuse.
            exec(compile(source, str(file), "exec"), mod.__dict__)
        except Exception:
            sys.modules.pop(name, None)
            raise
        self._modules[key] = mod
        return mod

    def _load_func(self, file: Path, func_name: str, dotted: str) -> Callable[..., Any]:
        mod = self._load_module(file)
        if not hasattr(mod, func_name):
            raise HelperNotFound(f"{dotted}: {file} has no {func_name}")
        fn: Callable[..., Any] = getattr(mod, func_name)
        return fn


@dataclass
class DiContainer:
    db: Any | None = None
    http: Any | None = None
    redis: Any | None = None
    mq: Any | None = None
    case: Any | None = None
    profile: Any | None = None
    ctx: Any | None = None

    def select(self, name: str) -> Any:
        if not hasattr(self, name):
            raise HelperError(f"no injectable named {name!r}")
        v = getattr(self, name)
        if v is None:
            raise HelperError(f"injectable {name!r} not bound for this case")
        return v


_RESERVED = {"db", "http", "redis", "mq", "case", "profile", "ctx"}


def invoke_helper(
    fn: Callable[..., Any], *, args: dict[str, Any], di: DiContainer
) -> Any:
    sig = inspect.signature(fn)
    accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    clashes = sorted(_RESERVED & args.keys())
    if clashes:
        raise HelperError(
            f"helper {fn.__name__}: args {clashes} clash with injected names; "
            f"{', '.join(sorted(_RESERVED))} are supplied by the runner"
        )
    unknown = sorted(k for k in args if k not in sig.parameters)
    if unknown and not accepts_any:
        accepted = ", ".join(p for p in sig.parameters if p not in _RESERVED) or "<none>"
        raise HelperError(
            f"helper {fn.__name__} got unexpected args {unknown}; accepts: {accepted}"
        )
    kwargs: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        if pname in _RESERVED:
            kwargs[pname] = di.select(pname)
        elif pname in args:
            kwargs[pname] = args[pname]
    if accepts_any:
        kwargs.update({k: v for k, v in args.items() if k not in kwargs})
    bound = sig.bind_partial(**kwargs)
    bound.apply_defaults()
    return fn(*bound.args, **bound.kwargs)
