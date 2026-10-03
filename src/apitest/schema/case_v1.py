"""Case YAML schema v1 — single source of truth."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IdSpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["int64", "string"]
    prefix: int | str | None = None


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str
    params: dict[str, Any] = Field(default_factory=dict)  # query string
    headers: dict[str, str] = Field(default_factory=dict)
    # Body kinds (at most one of body / body_template / form / raw; files may
    # accompany form for multipart uploads):
    body: Any | None = None  # JSON
    body_template: str | None = None  # JSON rendered from a Jinja file
    body_vars: dict[str, Any] = Field(default_factory=dict)
    form: dict[str, Any] | None = None  # application/x-www-form-urlencoded
    raw: str | None = None  # sent verbatim; set Content-Type in headers
    files: dict[str, str] = Field(default_factory=dict)  # field -> path relative to the case

    @model_validator(mode="after")
    def _one_body_kind(self) -> Request:
        kinds = [
            k
            for k, v in (
                ("body", self.body),
                ("body_template", self.body_template),
                ("form", self.form),
                ("raw", self.raw),
            )
            if v is not None
        ]
        if len(kinds) > 1:
            raise ValueError(f"request declares more than one body kind: {', '.join(kinds)}")
        if self.files and kinds and kinds != ["form"]:
            raise ValueError("files can only be combined with form (multipart)")
        return self


class Assert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A list of acceptable statuses is allowed in teardown steps only (see Case).
    status: int | Annotated[list[int], Field(min_length=1)] | None = None
    json_: dict[str, Any] = Field(default_factory=dict, alias="json")
    headers: dict[str, Any] = Field(default_factory=dict)  # name (case-insensitive) -> matcher
    text: Any | None = None  # matcher against the raw response text


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    service: str | None = None
    role: str | None = None
    request: Request
    extract: dict[str, str] = Field(default_factory=dict)
    assert_: Assert = Field(default_factory=Assert, alias="assert")


class PythonCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call: str
    args: dict[str, Any] = Field(default_factory=dict)


class WaitSpec(BaseModel):
    """Poll a verify entry until it passes or `timeout_s` elapses (for writes
    that land after the HTTP response: @Async, AFTER_COMMIT listeners, outbox)."""

    model_config = ConfigDict(extra="forbid")
    timeout_s: float = Field(gt=0)
    interval_s: float = Field(default=0.5, gt=0)


class DbVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str
    expect_rows: list[dict[str, Any]] | None = None
    expect_count: int | None = None
    wait: WaitSpec | None = None


class RedisVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    exists: bool | None = None
    value: Any | None = None
    ttl_between: tuple[int, int] | None = None
    unprefixed: bool = False
    wait: WaitSpec | None = None


class MqSnifferDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exchange: str
    routing_key: str
    as_: str = Field(alias="as")


class MqSnifferVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sniffer: str
    # count / count_min / count_max all apply to the `match`-filtered set.
    count: int | None = None
    count_min: int | None = None
    count_max: int | None = None
    match: dict[str, Any] | None = None
    wait: WaitSpec | None = None


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    db: list[str] = Field(default_factory=list)
    redis: list[str] = Field(default_factory=list)
    rabbitmq: dict[str, list[MqSnifferDecl]] = Field(default_factory=dict)
    python: list[PythonCall] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)


class Verify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    db: list[DbVerify] = Field(default_factory=list)
    redis: list[RedisVerify] = Field(default_factory=list)
    rabbitmq: list[MqSnifferVerify] = Field(default_factory=list)
    python: list[PythonCall] = Field(default_factory=list)


class RedisTeardown(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Keys the service under test wrote; deleted verbatim (no case prefix).
    delete: list[str] = Field(default_factory=list)


class Teardown(BaseModel):
    model_config = ConfigDict(extra="forbid")
    db: list[str] = Field(default_factory=list)
    redis: RedisTeardown = Field(default_factory=RedisTeardown)
    python: list[PythonCall] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)


class KnownDefect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str
    step: str
    at: str | None = None
    env: str | None = None


class DataPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    isolated: bool = False
    cleanup: str = ""
    resources: list[str] = Field(default_factory=list)


class Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    services: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    redis: bool = False
    rabbitmq: bool = False
    test_data: list[str] = Field(default_factory=list)


class CoverageRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    variant: str = "default"


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["v1"] = Field(alias="schema")
    name: str
    description: str | None = None
    service: str
    source_commit: str | None = None
    covers: list[CoverageRef] = Field(default_factory=list)
    expectation: Literal["confirmed", "pending"] = "confirmed"
    known_defects: list[KnownDefect] = Field(default_factory=list)
    mutates: bool | None = None
    data: DataPlan = Field(default_factory=DataPlan)
    requires: Requirements = Field(default_factory=Requirements)
    service_headers: dict[str, dict[str, str]] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    # Sent with every step (step headers override); rendered per step, so a
    # token extracted in setup works: {Authorization: "Bearer ${ctx.token}"}.
    headers: dict[str, str] = Field(default_factory=dict)
    # True: may run concurrently under --parallel. Unset/False: uses the shared
    # serial group, including cases that touch several services.
    parallel_safe: bool | None = None
    ids: dict[str, IdSpecModel] = Field(default_factory=dict)
    setup: Setup = Field(default_factory=Setup)
    steps: list[Step]
    verify: Verify = Field(default_factory=Verify)
    teardown: Teardown = Field(default_factory=Teardown)

    @model_validator(mode="after")
    def _step_identity(self) -> Case:
        if self.mutates is False:
            conflicts = []
            if (
                self.teardown.steps
                or self.teardown.python
                or self.teardown.db
                or self.teardown.redis.delete
                or self.data.cleanup.strip()
            ):
                conflicts.append("teardown or data.cleanup")
            setup_writes = [
                f"setup step {s.name!r} uses {s.request.method}"
                for s in self.setup.steps
                if s.request.method not in {"GET", "HEAD", "OPTIONS"}
            ]
            setup_writes += [
                f"setup.{kind}"
                for kind, present in (
                    ("db", self.setup.db),
                    ("redis", self.setup.redis),
                    ("rabbitmq", self.setup.rabbitmq),
                )
                if present
            ]
            if setup_writes:
                conflicts.append(f"setup writes ({', '.join(setup_writes)})")
            if self.data.resources:
                conflicts.append("data.resources")
            execute_writes = [
                f"step {s.name!r} uses {s.request.method}"
                for s in self.steps
                if s.request.method in {"PUT", "PATCH", "DELETE"}
            ]
            if execute_writes:
                conflicts.append(f"writes ({', '.join(execute_writes)})")
            if conflicts:
                raise ValueError(
                    "; ".join(f"mutates: false cannot be combined with {c}" for c in conflicts)
                )
        for step in self.setup.steps + self.steps:
            if isinstance(step.assert_.status, list):
                raise ValueError(
                    f"step {step.name!r}: assert.status accepts a list only in teardown steps"
                )
        names = [step.name for step in self.setup.steps + self.steps + self.teardown.steps]
        if len(names) != len(set(names)):
            raise ValueError("step names must be unique across setup, execute and teardown")
        executable = {step.name: step for step in self.steps}
        for defect in self.known_defects:
            if defect.step not in executable:
                raise ValueError(f"known_defects: unknown execute step {defect.step!r}")
            if defect.at == "response":  # the step got no response; needs no assertion
                continue
            step = executable[defect.step]
            locations = {"status"} if step.assert_.status is not None else set()
            locations.update(f"json.{p}" for p in step.assert_.json_)
            locations.update(f"headers.{p.lower()}" for p in step.assert_.headers)
            if step.assert_.text is not None:
                locations.add("text")
            locations.update(f"extract.{name}" for name in step.extract)
            if not locations or (defect.at is not None and defect.at not in locations):
                raise ValueError(f"known_defects: unknown assertion {defect.at!r}")
        return self
