from pathlib import Path

import pytest
import yaml

from apitest.credential_guard import find_literal_credentials, is_sensitive_key

PROFILE = Path("/ws/profiles/local.yaml")


def problems(text: str) -> list[str]:
    found = find_literal_credentials(yaml.safe_load(text), PROFILE)
    return [p.removeprefix(f"{PROFILE}: ") for p in found]


@pytest.mark.parametrize(
    "key",
    [
        "Authorization",
        "Cookie",
        "X-Api-Key",
        "api_key",
        "access_token",
        "client-secret",
        "login_password",
        "PASSWD",
        "credentials",
        "X-Signature",
        "private_key",
        "login_pwd",
        "ssh_passphrase",
    ],
)
def test_sensitive_keys(key):
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key",
    ["base_url", "auth_mode", "X-Env", "account", "extends", "dsn", "author", "session_timeout",
     "passport_no"],
)
def test_ordinary_keys(key):
    assert not is_sensitive_key(key)


def test_env_references_pass():
    assert problems(
        "databases:\n"
        "  example: { dsn: 'mysql://${U}:${P}@h/d?useSSL=false' }\n"
        "  whole: { dsn: '${TEST_MYSQL_DSN}' }\n"
        "  nopass: { dsn: 'mysql://root@h/d' }\n"
        "rabbitmq:\n  default: { url: 'amqp://guest:${LOCAL_MQ_PASS}@h/' }\n"
        "services:\n  orders:\n    base_url: '${LOCAL_ORDERS_URL}'\n"
        "    headers: { Authorization: 'Bearer ${TOKEN}', X-Env: stage }\n"
        "test_data:\n  login_password: '${LOCAL_LOGIN_PASSWORD}'\n  account: 42\n"
    ) == []


@pytest.mark.parametrize(
    ("text", "field", "name"),
    [
        (
            "databases:\n  example-payment: { dsn: 'mysql://u:hunter2@h/d' }\n",
            "databases.example-payment.dsn has a literal URL password",
            "LOCAL_EXAMPLE_PAYMENT_DB_PASS",
        ),
        (
            "databases:\n  example: { dsn: 'mysql://u:hun@ter:2@h/d' }\n",
            "databases.example.dsn has a literal URL password",
            "LOCAL_EXAMPLE_DB_PASS",
        ),
        (
            "databases:\n  example: { dsn: 'mysql://u:x${P}@h/d' }\n",
            "databases.example.dsn has a literal URL password",
            "LOCAL_EXAMPLE_DB_PASS",
        ),
        (
            "databases:\n  example: { dsn: 'mysql://h/d?useSSL=false&password=hunter2' }\n",
            "databases.example.dsn has a literal URL parameter 'password'",
            "LOCAL_EXAMPLE_DB_PASSWORD",
        ),
        (
            "rabbitmq:\n  default: { url: 'amqp://guest:hunter2@h/' }\n",
            "rabbitmq.default.url has a literal URL password",
            "LOCAL_MQ_PASS",
        ),
        (
            "redis:\n  default: { url: 'redis://:hunter2@h/0' }\n",
            "redis.default.url has a literal URL password",
            "LOCAL_REDIS_PASS",
        ),
        (
            "services:\n  orders: { base_url: 'http://svc:hunter2@orders' }\n",
            "services.orders.base_url has a literal URL password",
            "LOCAL_ORDERS_PASS",
        ),
        (
            "services:\n  orders:\n    headers: { X-Api-Key: hunter2 }\n",
            "services.orders.headers.X-Api-Key holds a literal value",
            "LOCAL_ORDERS_X_API_KEY",
        ),
        (
            "services:\n  orders:\n    roles:\n"
            "      admin: { headers: { Authorization: 'Basic hunter2' } }\n",
            "services.orders.roles.admin.headers.Authorization holds a literal value",
            "LOCAL_ORDERS_ADMIN_AUTHORIZATION",
        ),
        (
            "services:\n  orders:\n    headers: { Authorization: 'Bearer ${ctx.hunter2}' }\n",
            "services.orders.headers.Authorization holds a literal value",
            "LOCAL_ORDERS_AUTHORIZATION",
        ),
        (
            "test_data:\n  login_password: hunter2\n",
            "test_data.login_password holds a literal value",
            "LOCAL_LOGIN_PASSWORD",
        ),
        (
            "test_data:\n  users: [{ password: hunter2 }]\n",
            "test_data.users.0.password holds a literal value",
            "LOCAL_USERS_0_PASSWORD",
        ),
        (
            "test_data:\n  api_keys: [hunter2]\n",
            "test_data.api_keys.0 holds a literal value",
            "LOCAL_API_KEYS_0",
        ),
    ],
)
def test_literal_credentials_name_field_and_variable(text, field, name):
    [problem] = problems(text)
    fix = f"put the value in .env as {name}=... and write ${{{name}}} in its place"
    assert problem == f"{field}; {fix}"
    assert "hunter2" not in problem


@pytest.mark.parametrize(
    ("path", "text", "name"),
    [
        (PROFILE, "test_data:\n  登录password: x\n", "LOCAL_PASSWORD"),
        (Path("/ws/profiles/2024.yaml"), "test_data:\n  password: x\n", "_2024_PASSWORD"),
    ],
)
def test_suggested_names_are_valid_env_vars(path, text, name):
    [problem] = find_literal_credentials(yaml.safe_load(text), path)
    assert problem.endswith(f"as {name}=... and write ${{{name}}} in its place")


def test_problems_name_the_file():
    [problem] = find_literal_credentials({"secret": "x"}, PROFILE)
    assert problem.startswith(f"{PROFILE}: secret holds a literal value;")


def test_values_that_are_not_checked():
    assert problems(
        "test_data:\n  password: 123456\n  token_enabled: true\n  secret: ''\n  api_key: null\n"
        "  callback: 'http://[::1'\n"
    ) == []
    assert find_literal_credentials(None, PROFILE) == []
    assert find_literal_credentials(["a", "b"], PROFILE) == []
