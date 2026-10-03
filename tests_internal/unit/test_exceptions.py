from apitest.exceptions import (
    DependencyMissing,
    IsolationError,
    MissingExtract,
    VerifyError,
)


def test_verify_error_carries_message():
    e = VerifyError("ledger off by 5")
    assert "ledger off by 5" in str(e)

def test_missing_extract_lists_available():
    e = MissingExtract("order_id", available=["token", "user_id"])
    s = str(e)
    assert "order_id" in s and "token" in s and "user_id" in s

def test_isolation_error_is_runtime_error():
    assert issubclass(IsolationError, RuntimeError)

def test_dependency_missing_is_distinct_type():
    assert not issubclass(DependencyMissing, VerifyError)
