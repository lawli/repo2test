import pytest

from apitest.services import UnknownService, validate_service


def test_validate_service_accepts_known():
    validate_service("example-payment", {"example", "example-payment"})  # no raise


def test_validate_service_accepts_arbitrary_org_service():
    # No hardcoded org registry: the caller supplies the known set.
    validate_service("trans", {"trans", "billing"})  # no raise


def test_validate_service_rejects_unknown():
    with pytest.raises(UnknownService) as exc:
        validate_service("trans", {"example", "example-payment"})
    assert "trans" in str(exc.value)
    assert "example-payment" in str(exc.value)  # error lists known names
