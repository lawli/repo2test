from pathlib import Path

import pytest

from apitest.escape import AmbiguousHelper, HelperResolver


def _make_helper(path: Path, name: str, body: str = "def f(): return 'ok'") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    (path.parent / "__init__.py").touch()
    return path


def test_resolves_in_service_shared(tmp_path: Path):
    case = tmp_path / "tests/example-payment/scen/suite/case_001.yaml"
    case.parent.mkdir(parents=True, exist_ok=True)
    case.touch()
    _make_helper(tmp_path / "tests/example-payment/_shared/helpers/auth.py", "auth")
    r = HelperResolver(tests_root=tmp_path / "tests")
    fn = r.resolve("helpers.auth.f", case_path=case)
    assert fn() == "ok"


def test_ambiguous_without_override_errors(tmp_path: Path):
    case = tmp_path / "tests/example-payment/scen/suite/case_001.yaml"
    case.parent.mkdir(parents=True, exist_ok=True)
    case.touch()
    _make_helper(tmp_path / "tests/example-payment/_shared/helpers/x.py", "x")
    _make_helper(tmp_path / "tests/example-payment/scen/suite/_helpers/x.py", "x")
    r = HelperResolver(tests_root=tmp_path / "tests")
    with pytest.raises(AmbiguousHelper):
        r.resolve("helpers.x.f", case_path=case)


def test_override_marker_resolves_local(tmp_path: Path):
    case = tmp_path / "tests/example-payment/scen/suite/case_001.yaml"
    case.parent.mkdir(parents=True, exist_ok=True)
    case.touch()
    _make_helper(tmp_path / "tests/example-payment/_shared/helpers/x.py", "x")
    _make_helper(
        tmp_path / "tests/example-payment/scen/suite/_helpers/x.py",
        "x",
        body=(
            "from apitest.escape import override\n"
            "@override('helpers.x.f')\n"
            "def f(): return 'local'\n"
        ),
    )
    r = HelperResolver(tests_root=tmp_path / "tests")
    fn = r.resolve("helpers.x.f", case_path=case)
    assert fn() == "local"


def test_helper_module_state_persists_across_resolves(tmp_path: Path):
    case = tmp_path / "tests/example-payment/scen/case_001.yaml"
    case.parent.mkdir(parents=True, exist_ok=True)
    case.touch()
    _make_helper(
        tmp_path / "tests/example-payment/_shared/helpers/counter.py",
        "counter",
        body="calls = []\ndef bump():\n    calls.append(1)\n    return len(calls)\n",
    )
    r = HelperResolver(tests_root=tmp_path / "tests")
    assert r.resolve("helpers.counter.bump", case_path=case)() == 1
    assert r.resolve("helpers.counter.bump", case_path=case)() == 2


def test_helper_module_is_importable_via_sys_modules(tmp_path: Path):
    import sys

    case = tmp_path / "tests/example-payment/scen/case_001.yaml"
    case.parent.mkdir(parents=True, exist_ok=True)
    case.touch()
    _make_helper(tmp_path / "tests/example-payment/_shared/helpers/mod.py", "mod")
    fn = HelperResolver(tests_root=tmp_path / "tests").resolve("helpers.mod.f", case_path=case)
    assert fn.__module__ in sys.modules


def test_helper_module_state_persists_across_resolver_instances(tmp_path: Path):
    """The pytest plugin builds a new HelperResolver per case; a token cache in
    a helper module must survive from one case to the next."""
    case = tmp_path / "tests/example-payment/scen/case_001.yaml"
    case.parent.mkdir(parents=True, exist_ok=True)
    case.touch()
    _make_helper(
        tmp_path / "tests/example-payment/_shared/helpers/counter2.py",
        "counter2",
        body="calls = []\ndef bump():\n    calls.append(1)\n    return len(calls)\n",
    )
    first = HelperResolver(tests_root=tmp_path / "tests")
    second = HelperResolver(tests_root=tmp_path / "tests")
    assert first.resolve("helpers.counter2.bump", case_path=case)() == 1
    assert second.resolve("helpers.counter2.bump", case_path=case)() == 2


@pytest.mark.parametrize("reuse_resolver", [True, False])
def test_changed_helper_uses_current_source_even_with_stale_bytecode(tmp_path, reuse_resolver):
    import os
    import py_compile

    case = tmp_path / "tests/shop/scenario/case.yaml"
    case.parent.mkdir(parents=True)
    helper = _make_helper(
        tmp_path / "tests/shop/_shared/helpers/check.py", "check", "def f(): return 'old'\n"
    )
    stamp = helper.stat()
    py_compile.compile(str(helper), doraise=True)
    resolver = HelperResolver(tmp_path / "tests")
    assert resolver.resolve("helpers.check.f", case_path=case)() == "old"
    helper.write_text("def f(): return 'new'\n")
    os.utime(helper, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    if not reuse_resolver:
        resolver = HelperResolver(tmp_path / "tests")
    assert resolver.resolve("helpers.check.f", case_path=case)() == "new"
