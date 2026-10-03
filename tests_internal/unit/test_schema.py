import json

import pytest
from pydantic import ValidationError

from apitest.schema.case_v1 import Case


def _minimal_case() -> dict:
    return {
        "schema": "v1",
        "name": "ok",
        "service": "example-payment",
        "steps": [
            {
                "name": "do",
                "request": {"method": "GET", "path": "/api/x"},
                "assert": {"status": 200},
            }
        ],
    }


def test_minimal_case_validates():
    c = Case.model_validate(_minimal_case())
    assert c.service == "example-payment"
    assert c.steps[0].request.method == "GET"


def test_arbitrary_service_name_accepted():
    # Service names are no longer validated against a hardcoded list; any
    # string is accepted at the schema layer (org-agnostic).
    d = _minimal_case()
    d["service"] = "trans"
    c = Case.model_validate(d)
    assert c.service == "trans"


def test_schema_version_must_be_v1():
    d = _minimal_case()
    d["schema"] = "v2"
    with pytest.raises(ValidationError):
        Case.model_validate(d)


def test_id_spec_int64_requires_prefix_int():
    d = _minimal_case()
    d["ids"] = {"user_id": {"kind": "int64", "prefix": 70}}
    Case.model_validate(d)


def test_python_step_requires_call_field():
    d = _minimal_case()
    d["setup"] = {"python": [{"args": {}}]}
    with pytest.raises(ValidationError):
        Case.model_validate(d)


def test_json_schema_export_round_trips(tmp_path):
    from apitest.schema.export import export_schema
    out = tmp_path / "case.schema.v1.json"
    export_schema(out)
    schema = json.loads(out.read_text())
    assert schema["title"] == "Case"
    assert "service" in schema["properties"]


@pytest.mark.parametrize(
    "block",
    [
        {"verify": {"db": [{"sql": "SELECT 1", "match": {"a": 1}}]}},
        {"verify": {"db": [{"sql": "SELECT 1", "ordered": True}]}},
        {"verify": {"rabbitmq": [{"sniffer": "s", "count_total": 1}]}},
        {"verify": {"rabbitmq": [{"sniffer": "s", "count_between": [1, 2]}]}},
        {"ids": {"x": {"kind": "snowflake"}}},
    ],
)
def test_fields_the_runner_never_honoured_are_rejected(block):
    d = _minimal_case()
    d.update(block)
    with pytest.raises(ValidationError):
        Case.model_validate(d)


def test_mq_count_min_max_accepted():
    d = _minimal_case()
    d["verify"] = {"rabbitmq": [{"sniffer": "s", "count_min": 1, "count_max": 3}]}
    c = Case.model_validate(d)
    assert (c.verify.rabbitmq[0].count_min, c.verify.rabbitmq[0].count_max) == (1, 3)
