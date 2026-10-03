import re
from pathlib import Path

import pytest

from apitest.profile import LiteralCredentials, ProfileError, load_profile
from apitest.taint import is_tainted


def test_load_simple_profile(tmp_path):
    (tmp_path / "p.yaml").write_text(
        "services:\n  example:\n    base_url: https://example.example.com\n    auth_mode: jwt\n"
        "databases:\n  example:\n    dsn: mysql://u@h:3306/example\n"
    )
    p = load_profile("p", profiles_dir=tmp_path)
    assert p.services["example"]["base_url"] == "https://example.example.com"


def test_extends_layers_base(tmp_path):
    (tmp_path / "base.yaml").write_text(
        "services:\n  example:\n    base_url: BASE\n    auth_mode: jwt\n"
    )
    (tmp_path / "child.yaml").write_text(
        "extends: base.yaml\nservices:\n  example:\n    base_url: CHILD\n"
    )
    p = load_profile("child", profiles_dir=tmp_path)
    assert p.services["example"]["base_url"] == "CHILD"
    assert p.services["example"]["auth_mode"] == "jwt"


def test_multilevel_inheritance_preserves_grandparent_and_child_overrides(tmp_path):
    (tmp_path / "base.yaml").write_text(
        "environment: non-production\nservices:\n  shop:\n    base_url: http://base\n"
        "    headers: {x-base: base, x-shared: base}\nhttp: {timeout: 12}\n"
        "test_data: {account: 42}\n"
    )
    (tmp_path / "middle.yaml").write_text(
        "extends: base.yaml\nservices:\n  shop:\n"
        "    headers: {x-middle: middle, x-shared: middle}\n"
    )
    (tmp_path / "child.yaml").write_text(
        "extends: middle.yaml\nservices:\n  shop:\n    base_url: http://child\n"
        "    headers: {x-shared: child}\n"
    )
    p = load_profile("child", tmp_path)
    assert p.environment is None
    assert p.services["shop"] == {
        "base_url": "http://child",
        "headers": {
            "x-base": "base",
            "x-middle": "middle",
            "x-shared": "child",
        },
    }
    assert p.http == {"timeout": 12} and p.test_data == {"account": 42}


@pytest.mark.parametrize("parent", ["a.yaml", "b.yaml"])
def test_inheritance_cycles_report_the_chain(tmp_path, parent):
    (tmp_path / "a.yaml").write_text(f"extends: {parent}\n")
    (tmp_path / "b.yaml").write_text("extends: a.yaml\n")
    with pytest.raises(ProfileError, match="inheritance cycle"):
        load_profile("a", tmp_path)


def test_env_var_substitution_taints(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PASS", "hunter2")
    (tmp_path / "p.yaml").write_text(
        "databases:\n  example:\n    dsn: mysql://u:${DB_PASS}@h/example\n"
    )
    p = load_profile("p", profiles_dir=tmp_path)
    dsn = p.databases["example"]["dsn"]
    assert "hunter2" in dsn
    assert is_tainted(dsn)


def test_missing_env_var_refuses_to_start(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    (tmp_path / "p.yaml").write_text(
        "databases:\n  example:\n    dsn: mysql://${MISSING_VAR}@h/example\n"
    )
    with pytest.raises(ProfileError) as exc:
        load_profile("p", profiles_dir=tmp_path)
    assert "MISSING_VAR" in str(exc.value)


def test_arbitrary_service_name_accepted(tmp_path):
    # The profile is the registry: any service name is valid (org-agnostic).
    (tmp_path / "p.yaml").write_text(
        "services:\n  trans:\n    base_url: https://trans.example.com\n    auth_mode: jwt\n"
    )
    p = load_profile("p", profiles_dir=tmp_path)
    assert p.services["trans"]["base_url"] == "https://trans.example.com"


def test_database_matching_custom_service_accepted(tmp_path):
    (tmp_path / "p.yaml").write_text(
        "services:\n  billing:\n    base_url: https://billing.example.com\n"
        "databases:\n  billing:\n    dsn: mysql://u@h:3306/billing\n"
    )
    p = load_profile("p", profiles_dir=tmp_path)
    assert "billing" in p.databases


def test_database_without_matching_service_rejected(tmp_path):
    (tmp_path / "p.yaml").write_text(
        "services:\n  billing:\n    base_url: https://billing.example.com\n"
        "databases:\n  typo:\n    dsn: mysql://u@h:3306/billing\n"
    )
    with pytest.raises(ProfileError) as exc:
        load_profile("p", profiles_dir=tmp_path)
    assert "typo" in str(exc.value)


def test_literal_credential_refuses_to_load(tmp_path):
    (tmp_path / "p.yaml").write_text("rabbitmq:\n  default: { url: 'amqp://guest:hunter2@h/' }\n")
    with pytest.raises(LiteralCredentials) as exc:
        load_profile("p", profiles_dir=tmp_path)
    assert isinstance(exc.value, ProfileError)
    assert "rabbitmq.default.url has a literal URL password" in str(exc.value)
    assert "hunter2" not in str(exc.value)


def test_literal_credential_in_parent_refuses_even_when_child_overrides(tmp_path):
    # The parent file is committed as-is, so overriding its value does not help.
    (tmp_path / "base.yaml").write_text(
        "services:\n  shop:\n    headers: { Authorization: 'Basic hunter2' }\n"
    )
    (tmp_path / "child.yaml").write_text(
        "extends: base.yaml\nservices:\n  shop:\n    headers: { Authorization: '${TOKEN}' }\n"
    )
    with pytest.raises(LiteralCredentials) as exc:
        load_profile("child", tmp_path, resolve=False)
    assert exc.value.problems == [
        f"{(tmp_path / 'base.yaml').resolve()}: services.shop.headers.Authorization holds a "
        "literal value; put the value in .env as BASE_SHOP_AUTHORIZATION=... and write "
        "${BASE_SHOP_AUTHORIZATION} in its place"
    ]


def test_invalid_yaml_is_a_profile_error_without_the_source_line(tmp_path):
    (tmp_path / "p.yaml").write_text(
        'services:\n  shop:\n    headers: { Authorization: "Bearer hunter2 }\n'
    )
    with pytest.raises(ProfileError) as exc:
        load_profile("p", tmp_path)
    assert str(exc.value).startswith("invalid profile YAML:")
    assert "p.yaml" in str(exc.value)
    assert "hunter2" not in str(exc.value)


REPO = Path(__file__).resolve().parents[2]
ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


@pytest.mark.parametrize("name", ["base", "example", "local", "staging"])
def test_example_profiles_load_and_prefix_their_variables(name):
    load_profile(name, REPO / "profiles", resolve=False)
    text = (REPO / "profiles" / f"{name}.yaml").read_text()
    assert all(ref.startswith(f"{name.upper()}_") for ref in ENV_REF.findall(text)), text


def test_env_example_defines_exactly_the_example_profile_variables():
    texts = (p.read_text() for p in (REPO / "profiles").glob("*.yaml"))
    refs = set(ENV_REF.findall("".join(texts)))
    lines = (REPO / ".env.example").read_text().splitlines()
    defined = {line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")}
    assert defined == refs
