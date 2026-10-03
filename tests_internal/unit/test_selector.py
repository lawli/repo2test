from pathlib import Path

import pytest

from apitest.selector import AmbiguousSelector, resolve_selector


def _setup(tmp_path: Path) -> Path:
    root = tmp_path / "tests"
    (root / "example-payment/create_order/happy_path").mkdir(parents=True)
    (root / "example-payment/create_order/happy_path/case_001_basic.yaml").write_text(
        "schema: v1\nname: case_001_basic\nservice: example-payment\nsteps: []\n"
    )
    (root / "example-payment/create_order/happy_path/case_002.yaml").write_text(
        "schema: v1\nname: case_002\nservice: example-payment\nsteps: []\n"
    )
    (root / "example-payment/refund").mkdir(parents=True)
    (root / "example-payment/refund/case_010.yaml").write_text(
        "schema: v1\nname: case_010\nservice: example-payment\nsteps: []\n"
    )
    return root


def test_resolve_full_id(tmp_path: Path) -> None:
    root = _setup(tmp_path)
    ids = resolve_selector("example-payment/create_order/happy_path/case_001", tests_root=root)
    assert len(ids) == 1
    assert ids[0].endswith("case_001_basic.yaml::case_001_basic")


def test_resolve_prefix_path(tmp_path: Path) -> None:
    root = _setup(tmp_path)
    ids = resolve_selector("example-payment/create_order", tests_root=root)
    assert len(ids) == 2


def test_ambiguous_case_prefix(tmp_path: Path) -> None:
    root = _setup(tmp_path)
    (root / "example-payment/create_order/happy_path/case_0010.yaml").write_text(
        "schema: v1\nname: case_0010\nservice: example-payment\nsteps: []\n"
    )
    with pytest.raises(AmbiguousSelector):
        resolve_selector("example-payment/create_order/happy_path/case_001", tests_root=root)


def test_resolve_service_only(tmp_path: Path) -> None:
    root = _setup(tmp_path)
    ids = resolve_selector("example-payment", tests_root=root)
    assert len(ids) == 3


def test_is_case_file_only_matches_case_prefixed_yaml_outside_helper_dirs() -> None:
    from apitest.selector import is_case_file

    assert is_case_file(Path("tests/svc/scen/case_001_ok.yaml"))
    assert not is_case_file(Path("tests/svc/scen/fixtures/redis/seed.yaml"))
    assert not is_case_file(Path("tests/svc/scen/notes.yaml"))
    assert not is_case_file(Path("tests/svc/_shared/case_001.yaml"))
    assert not is_case_file(Path("tests/svc/scen/_helpers/case_001.yaml"))
    assert not is_case_file(Path("tests/svc/scen/case_001.yml"))


def test_resolve_selector_ignores_non_case_yaml(tmp_path: Path) -> None:
    root = _setup(tmp_path)
    seed = root / "example-payment/refund/fixtures/redis/seed.yaml"
    seed.parent.mkdir(parents=True)
    seed.write_text("set:\n  - { key: k, value: v }\n")
    ids = resolve_selector("example-payment", tests_root=root)
    assert len(ids) == 3
    assert not any("seed" in i for i in ids)
