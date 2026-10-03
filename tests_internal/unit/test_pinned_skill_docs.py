from pathlib import Path

import pytest

import apitest

AUTHORING = (
    Path(apitest.__file__).parent / "assets/skills/repo2test-workspace/references/authoring.md"
)
ENVIRONMENT = AUTHORING.with_name("environment.md")


def _words(path: Path) -> str:
    """The file as one line, so a phrase is found however the prose is wrapped."""
    return " ".join(path.read_text().split())


@pytest.mark.parametrize(
    "matcher",
    [
        "@any",
        "@absent",
        "@null",
        "@type",
        "@number",
        "@len",
        "@contains",
        "@in",
        "@regex",
        "@uuid",
        "@iso8601",
        "@@",
    ],
)
def test_pinned_authoring_reference_documents_each_matcher(matcher: str) -> None:
    assert matcher in _words(AUTHORING)


def test_pinned_environment_reference_shows_how_to_reference_test_data() -> None:
    text = _words(ENVIRONMENT)
    assert "[test_data]" in text
    assert "${ctx.profile.test_data." in text


def test_pinned_authoring_reference_says_what_is_rendered_and_what_is_literal() -> None:
    text = _words(AUTHORING)
    assert "Jinja expression" in text
    assert "helper `args` are literal" in text


def test_pinned_authoring_reference_defines_evidence_location_and_normalized_path() -> None:
    text = _words(AUTHORING)
    assert "relative to the checkout root" in text
    assert "trailing slash" in text


def test_pinned_execution_reference_documents_the_response_location() -> None:
    text = _words(AUTHORING.with_name("execution.md"))
    assert "`at: response`" in text


def test_pinned_authoring_reference_documents_the_supplied_route_pattern() -> None:
    text = _words(AUTHORING)
    assert "--pattern" in text and "--include" in text


def test_pinned_authoring_reference_explains_how_to_build_and_check_a_route_pattern() -> None:
    text = _words(AUTHORING)
    assert "cites the same file and line" in text  # why the pattern must hit the cited line
    assert "multiline" in text  # which regex dialect and mode
    assert "`files` 0" in text and "`mappings` 0" in text  # how to read the result
    assert "by file location" in text  # what a pattern cannot see


def test_pinned_authoring_reference_says_how_a_pattern_run_can_end() -> None:
    text = _words(AUTHORING)
    assert "unmatched_evidence" in text  # the mechanical check that a pattern is too narrow
    assert "do not cite it as route evidence" in text  # a match that is not a route
    assert "names its method" in text  # which line to cite in a chained registration


def test_pinned_authoring_reference_lists_the_fragment_field_values() -> None:
    text = _words(AUTHORING)
    for value in ("implementation", "needs-review", "`conditions` is a list"):
        assert value in text


def test_pinned_authoring_reference_closes_the_gaps_an_express_run_found() -> None:
    text = _words(AUTHORING)
    assert "prints its path as `checkout`" in text  # what <checkout> is
    assert "one row with `status: gap`" in text  # how a discovery gap is recorded
    assert "on the endpoint or on a row" in text  # where route evidence counts
    assert "exits 2 while it lists one" in text  # the exit code of a listed registration
    assert "do not narrow it to names" in text  # a pattern describes a form
    assert "`**` also matches no directory" in text  # what a directory glob reaches


def test_pinned_skill_says_when_doctor_passes() -> None:
    assert "`problems` is empty" in _words(AUTHORING.parent.parent / "SKILL.md")


def test_pinned_skill_says_how_repository_tests_count_as_evidence() -> None:
    text = _words(AUTHORING)
    assert "`requirement`, `declarative`, `test` or `implementation`" in text
    assert "never to overrule one" in text
    assert "`test_backed`" in _words(AUTHORING.parent.parent / "SKILL.md")
