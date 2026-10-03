from pathlib import Path

from apitest.secret_scan import scan_file, scan_text


def test_aws_key_detected():
    findings = scan_text("aws=AKIAABCDEFGHIJKLMNOP", path=Path("x.yaml"))
    assert any("AWS" in f.kind for f in findings)


def test_dsn_with_password_detected():
    findings = scan_text("dsn: mysql://user:hunter2@db.example/app", path=Path("x.yaml"))
    assert any("DSN" in f.kind for f in findings)


def test_jwt_three_segments_detected():
    long = "a" * 40
    jwt = f"{long}.{long}.{long}"
    findings = scan_text(f"token: {jwt}", path=Path("x.yaml"))
    assert any("JWT" in f.kind for f in findings)


def test_allow_marker_suppresses(tmp_path: Path):
    f = tmp_path / "x.yaml"
    f.write_text("dsn: mysql://u:p@h/d  # apitest: allow-secret-pattern\n")
    findings = scan_file(f)
    assert findings == []


def test_clean_text_no_findings():
    assert scan_text("just a comment", path=Path("x.yaml")) == []
