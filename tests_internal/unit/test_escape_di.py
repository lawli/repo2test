from dataclasses import dataclass
from typing import Any

from apitest.ctx import Case
from apitest.escape import DiContainer, invoke_helper


@dataclass
class FakeDb:
    name: str = "db"


@dataclass
class FakeHttp:
    base: str = "http"


def fn_with_db(db: Any, order_id: Any) -> str:
    return f"{db.name}:{order_id}"


def fn_with_http_and_case(http: Any, case: Any, qty: Any) -> str:
    return f"{http.base}:{case.id_short}:{qty}"


def test_di_injects_by_param_name() -> None:
    di = DiContainer(db=FakeDb(), http=FakeHttp(), case=Case(id="x", id_short="abc"))
    assert invoke_helper(fn_with_db, args={"order_id": "O-1"}, di=di) == "db:O-1"


def test_di_combines_args_and_injectables() -> None:
    di = DiContainer(db=FakeDb(), http=FakeHttp(), case=Case(id="x", id_short="abc"))
    assert invoke_helper(fn_with_http_and_case, args={"qty": 2}, di=di) == "http:abc:2"


def test_unknown_arg_raises_instead_of_being_dropped() -> None:
    import pytest

    from apitest.escape import HelperError

    di = DiContainer(db=FakeDb(), http=FakeHttp(), case=Case(id="x", id_short="abc"))
    with pytest.raises(HelperError, match="order_idd"):
        invoke_helper(fn_with_db, args={"order_idd": "O-1"}, di=di)


def fn_with_kwargs(db: Any, **extra: Any) -> str:
    return f"{db.name}:{sorted(extra)}"


def test_var_kwargs_accepts_any_arg() -> None:
    di = DiContainer(db=FakeDb())
    assert invoke_helper(fn_with_kwargs, args={"a": 1, "b": 2}, di=di) == "db:['a', 'b']"


def test_arg_named_like_an_injectable_is_rejected() -> None:
    import pytest

    from apitest.escape import HelperError

    di = DiContainer(db=FakeDb(), http=FakeHttp(), case=Case(id="x", id_short="abc"))
    with pytest.raises(HelperError, match="db"):
        invoke_helper(fn_with_db, args={"db": "FROM_YAML", "order_id": "O-1"}, di=di)
