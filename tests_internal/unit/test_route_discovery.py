import pytest
import yaml

from apitest.coverage import fastapi_mappings, reconcile, spring_mappings


@pytest.mark.parametrize("package", ["test", "tests", "build", "target"])
def test_production_package_names_are_not_treated_as_output_or_test_roots(tmp_path, package):
    controller = tmp_path / f"module/src/main/java/com/example/{package}/Orders.java"
    controller.parent.mkdir(parents=True)
    controller.write_text('@GetMapping("/orders")\nclass Orders {}\n')
    test_source = tmp_path / "module/src/test/java/Example.java"
    test_source.parent.mkdir(parents=True)
    test_source.write_text('@GetMapping("/test-only")\nclass Example {}\n')
    found = spring_mappings(tmp_path)
    assert len(found) == 1 and found[0]["file"] == controller.relative_to(tmp_path).as_posix()


@pytest.mark.parametrize("extension", ["java", "kt"])
def test_character_literals_comments_and_text_blocks_preserve_later_mappings(tmp_path, extension):
    source = tmp_path / f"Orders.{extension}"
    source.write_text(
        "char quote = '\"';\n"
        'String example = "@GetMapping(ignored)";\n'
        'String block = """\n@GetMapping("ignored")\n""";\n'
        '/* @GetMapping("ignored") */\n'
        '// @GetMapping("ignored")\n'
        '@GetMapping("/real")\n'
        "void real() {}\n"
    )
    assert [(item["line"], item["syntax"]) for item in spring_mappings(tmp_path)] == [
        (8, "@GetMapping")
    ]


def test_jvm_comment_nesting_matches_the_source_language(tmp_path):
    (tmp_path / "Java.java").write_text('/* inner /* closes here */\n@GetMapping("/java")\n')
    (tmp_path / "Kotlin.kt").write_text(
        '/* outer /* inner */ @GetMapping("ignored") */\n@GetMapping("/kotlin")\n'
    )
    assert [(item["file"], item["line"]) for item in spring_mappings(tmp_path)] == [
        ("Java.java", 2),
        ("Kotlin.kt", 2),
    ]


def test_management_and_conditional_routes_remain_reconciliation_gaps(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "Management.java").write_text(
        '@Endpoint(id = "inventory")\n'
        '@ConditionalOnProperty(name = "inventory.enabled", havingValue = "true")\n'
        "class Inventory {\n"
        '  @ReadOperation\n  public String status() { return "UP"; }\n'
        "  @WriteOperation\n  public void refresh() {}\n}\n"
    )
    (source / "Optional.java").write_text(
        '@ConditionalOnProperty(name = "optional.enabled")\n'
        '@GetMapping("/optional")\nclass Optional {}\n'
    )
    inventory = tmp_path / "coverage"
    inventory.mkdir()
    result = reconcile(source, inventory)
    assert {item["syntax"] for item in result["discovery_gaps"]} == {
        "@Endpoint",
        "@ReadOperation",
        "@WriteOperation",
        "@GetMapping",
    }
    assert len(result["mappings"]) == 4


def test_reconcile_names_the_cross_check_it_performed(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "Orders.java").write_text('@GetMapping("/orders")\nclass Orders {}\n')
    inventory = tmp_path / "coverage"
    inventory.mkdir()
    assert reconcile(source, inventory)["cross_check"] == "spring"


def test_reconcile_says_when_a_source_has_nothing_to_cross_check(tmp_path):
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    (source / "app/main.py").write_text('@router.get("/items")\ndef items(): ...\n')
    inventory = tmp_path / "coverage"
    inventory.mkdir()
    result = reconcile(source, inventory)
    assert result["mappings"] == [] and result["discovery_gaps"] == []
    assert result["cross_check"] == "unavailable"


FASTAPI_ROUTES = (
    "from fastapi import APIRouter\n"
    "\n"
    "router = APIRouter()\n"
    "\n"
    "\n"
    '@router.get("/items")\n'
    "def items(): ...\n"
    "\n"
    "\n"
    "@router.post(\n"
    '    "/items",\n'
    "    status_code=201,\n"
    ")\n"
    "def create(): ...\n"
)


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _inventory(tmp_path, *route_evidence):
    """An inventory with one endpoint citing the given (file, line) route evidence."""
    inventory = tmp_path / "coverage"
    inventory.mkdir()
    if route_evidence:
        endpoint = {
            "service": "shop",
            "method": "GET",
            "path": "/items",
            "evidence": [{"file": f, "line": n, "kind": "route"} for f, n in route_evidence],
            "rows": [],
        }
        (inventory / "items.yaml").write_text(yaml.safe_dump(endpoint))
    return inventory


def test_fastapi_route_decorators_are_found_at_their_first_line(tmp_path):
    _write(tmp_path, "app/routes/items.py", FASTAPI_ROUTES)
    assert [(m["file"], m["line"], m["syntax"]) for m in fastapi_mappings(tmp_path)] == [
        ("app/routes/items.py", 6, "@router.get("),
        ("app/routes/items.py", 10, "@router.post("),
    ]


def test_fastapi_scan_skips_test_modules(tmp_path):
    _write(tmp_path, "app/routes/items.py", FASTAPI_ROUTES)
    patched = 'from unittest import mock\n\n\n@mock.patch("app.x")\ndef check(): ...\n'
    for name in ("tests/test_items.py", "tests/items_test.py", "tests/conftest.py"):
        _write(tmp_path, name, patched)
    assert {m["file"] for m in fastapi_mappings(tmp_path)} == {"app/routes/items.py"}


def test_reconcile_cross_checks_fastapi_routes_against_the_inventory(tmp_path):
    source = tmp_path / "source"
    _write(source, "app/routes/items.py", FASTAPI_ROUTES)
    result = reconcile(source, _inventory(tmp_path, ("app/routes/items.py", 6)))
    assert result["cross_check"] == "fastapi"
    assert [(g["file"], g["line"], g["scanner"]) for g in result["discovery_gaps"]] == [
        ("app/routes/items.py", 10, "fastapi")
    ]


EXPRESS_ROUTES = 'router.get("/orders", list);\nrouter.post("/orders", create);\n'
EXPRESS_PATTERN = r"\brouter\.(?:get|post)\("


def _express_source(tmp_path):
    source = tmp_path / "source"
    _write(source, "src/routes/orders.ts", EXPRESS_ROUTES)
    _write(source, "src/util.ts", 'cache.get("key");\n')
    _write(source, "node_modules/dep/index.ts", 'router.get("/dependency");\n')
    return source


def test_reconcile_scans_a_supplied_pattern_in_the_included_files(tmp_path):
    result = reconcile(
        _express_source(tmp_path),
        _inventory(tmp_path, ("src/routes/orders.ts", 1)),
        patterns=[EXPRESS_PATTERN],
        include=["*.ts"],
    )
    assert result["cross_check"] == "pattern"
    assert [(m["file"], m["line"], m["syntax"]) for m in result["mappings"]] == [
        ("src/routes/orders.ts", 1, "router.get("),
        ("src/routes/orders.ts", 2, "router.post("),
    ]
    assert [g["line"] for g in result["discovery_gaps"]] == [2]
    assert result["pattern"] == {
        "patterns": [EXPRESS_PATTERN],
        "include": ["*.ts"],
        "files": 2,
        "mappings": 2,
        "unmatched_evidence": [],
    }


def test_include_with_a_directory_is_relative_to_the_source(tmp_path):
    source = _express_source(tmp_path)
    _write(source, "scripts/seed.ts", 'router.get("/not-a-route");\n')
    result = reconcile(
        source, _inventory(tmp_path), patterns=[EXPRESS_PATTERN], include=["src/**/*.ts"]
    )
    assert {m["file"] for m in result["mappings"]} == {"src/routes/orders.ts"}


def test_supplied_pattern_without_matches_leaves_the_cross_check_unavailable(tmp_path):
    result = reconcile(
        _express_source(tmp_path), _inventory(tmp_path), patterns=["@Route"], include=["*.ts"]
    )
    assert result["cross_check"] == "unavailable"
    assert result["pattern"]["files"] == 2 and result["pattern"]["mappings"] == 0


def test_reconcile_names_every_scanner_that_found_routes(tmp_path):
    source = _express_source(tmp_path)
    _write(source, "Orders.java", '@GetMapping("/orders")\nclass Orders {}\n')
    result = reconcile(source, _inventory(tmp_path), patterns=[EXPRESS_PATTERN], include=["*.ts"])
    assert result["cross_check"] == "spring+pattern"


def test_reconcile_without_a_pattern_reports_no_pattern_block(tmp_path):
    source = tmp_path / "source"
    _write(source, "Orders.java", '@GetMapping("/orders")\nclass Orders {}\n')
    assert "pattern" not in reconcile(source, _inventory(tmp_path))


def test_reconcile_rejects_a_pattern_that_is_not_a_regex(tmp_path):
    with pytest.raises(ValueError, match="--pattern"):
        reconcile(_express_source(tmp_path), _inventory(tmp_path), patterns=["("], include=["*.ts"])


@pytest.mark.parametrize("glob", ["/etc/*.conf", "../*.ts"])
def test_reconcile_rejects_an_include_outside_the_source(tmp_path, glob):
    with pytest.raises(ValueError, match="--include"):
        reconcile(_express_source(tmp_path), _inventory(tmp_path), patterns=["x"], include=[glob])


CHAINED_ROUTES = "router\n  .route('/users')\n  .get(list)\n  .post(create);\n"


def test_supplied_pattern_lists_cited_route_lines_it_did_not_match(tmp_path):
    source = _express_source(tmp_path)
    _write(source, "src/routes/users.ts", CHAINED_ROUTES)
    inventory = _inventory(
        tmp_path,
        ("src/routes/orders.ts", 1),
        ("src/routes/orders.ts", 2),
        ("src/routes/users.ts", 3),
        ("src/routes/users.ts", 4),
    )
    result = reconcile(source, inventory, patterns=[EXPRESS_PATTERN], include=["src/**/*.ts"])
    assert result["discovery_gaps"] == []
    assert result["pattern"]["unmatched_evidence"] == [
        {"file": "src/routes/users.ts", "line": 3},
        {"file": "src/routes/users.ts", "line": 4},
    ]


def test_supplied_patterns_that_match_every_cited_route_line_leave_nothing_unmatched(tmp_path):
    source = _express_source(tmp_path)
    _write(source, "src/routes/users.ts", CHAINED_ROUTES)
    inventory = _inventory(
        tmp_path,
        ("src/routes/orders.ts", 1),
        ("src/routes/orders.ts", 2),
        ("src/routes/users.ts", 3),
        ("src/routes/users.ts", 4),
    )
    chained = r"^\s+\.(?:get|post)\("
    result = reconcile(
        source, inventory, patterns=[EXPRESS_PATTERN, chained], include=["src/**/*.ts"]
    )
    assert result["discovery_gaps"] == []
    assert result["pattern"]["unmatched_evidence"] == []
