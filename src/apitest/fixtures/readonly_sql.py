"""Conservative SELECT grammar for verification, checked again after interpolation.

This is an admission filter, not a general SQL parser. The database also executes
verification on a separate connection in a READ ONLY transaction. Unsupported
SQL belongs in a Python helper, which is subject to the write prerequisites.
"""

from __future__ import annotations

import re

# A backslash may escape anything except a quote: `\'` ends a string in different
# places with and without NO_BACKSLASH_ESCAPES, so it stays unsupported.
_TOKEN = re.compile(
    r"\s+|--(?=\s|$)[^\n]*|\#[^\n]*|/\*.*?\*/|"
    r"'(?:''|\\\\|\\[^'\\]|[^'\\])*'|\"(?:\"\"|\\\\|\\[^\"\\]|[^\"\\])*\"|`(?:``|[^`\\])*`|"
    r"[A-Za-z_][A-Za-z_0-9$]*|\d+(?:\.\d+)?|[(),.;*+/%=<>!|&^~-]",
    re.S,
)
_FUNCTIONS = set(
    [
        "ABS",
        "AVG",
        "BIN_TO_UUID",
        "DAY",
        "DAYOFMONTH",
        "FROM_UNIXTIME",
        "HEX",
        "HOUR",
        "INSTR",
        "LEFT",
        "LOCATE",
        "LPAD",
        "MINUTE",
        "MONTH",
        "RIGHT",
        "RPAD",
        "SECOND",
        "STR_TO_DATE",
        "UNHEX",
        "UNIX_TIMESTAMP",
        "YEAR",
        "CAST",
        "CEIL",
        "CEILING",
        "CHAR_LENGTH",
        "COALESCE",
        "CONCAT",
        "CONCAT_WS",
        "CONVERT",
        "COUNT",
        "CURDATE",
        "CURRENT_DATE",
        "CURRENT_TIME",
        "CURRENT_TIMESTAMP",
        "DATE",
        "DATEDIFF",
        "DATE_FORMAT",
        "DATE_ADD",
        "DATE_SUB",
        "EXTRACT",
        "FLOOR",
        "GREATEST",
        "GROUP_CONCAT",
        "IF",
        "IFNULL",
        "JSON_ARRAY",
        "JSON_CONTAINS",
        "JSON_EXTRACT",
        "JSON_LENGTH",
        "JSON_OBJECT",
        "JSON_TYPE",
        "JSON_UNQUOTE",
        "LEAST",
        "LENGTH",
        "LOWER",
        "LTRIM",
        "MAX",
        "MIN",
        "MOD",
        "NOW",
        "NULLIF",
        "REPLACE",
        "ROUND",
        "RTRIM",
        "SUBSTR",
        "SUBSTRING",
        "SUM",
        "TIME",
        "TIMESTAMPDIFF",
        "TRIM",
        "UPPER",
        "UTC_TIMESTAMP",
    ]
)
_GROUPS = {
    "IN",
    "EXISTS",
    "NOT",
    "AND",
    "OR",
    "XOR",
    "WHERE",
    "ON",
    "HAVING",
    "SELECT",
    "WHEN",
    "FROM",
    "JOIN",
}
_FORBIDDEN = set(
    [
        "INTO",
        "OUTFILE",
        "DUMPFILE",
        "FOR",
        "LOCK",
        "PROCEDURE",
        "INSERT",
        "UPDATE",
        "DELETE",
        "REPLACE",
        "ALTER",
        "CREATE",
        "DROP",
        "TRUNCATE",
        "CALL",
        "SET",
        "DO",
        "LOAD",
        "GRANT",
        "REVOKE",
        "HANDLER",
        "ANALYZE",
        "OPTIMIZE",
        "REPAIR",
    ]
)
# REPLACE(...) is a pure string function, but REPLACE as a statement remains disallowed.
_FORBIDDEN.remove("REPLACE")


def check_verification_sql(sql: str, *, template: bool = False) -> None:
    if template:
        sql = re.sub(r"\$\{[^}]+\}", "0", sql)
    tokens: list[str] = []
    pos = 0
    while pos < len(sql):
        match = _TOKEN.match(sql, pos)
        if match is None:
            raise ValueError("verify.db.sql: unsupported token; use a single read-only SELECT")
        token = match.group()
        if sql.startswith("/*", pos) and not token.startswith("/*"):
            raise ValueError("verify.db.sql: unterminated comment")
        pos = match.end()
        if token.startswith("/*"):
            if token.startswith(("/*!", "/*M!", "/*m!")) or "/*" in token[2:]:
                raise ValueError("verify.db.sql: executable or nested comments are forbidden")
            continue
        if token.isspace() or token.startswith(("--", "#")):
            continue
        tokens.append(token.upper() if token[0].isalpha() else token)
    if tokens and tokens[-1] == ";":
        tokens.pop()
    if not tokens or tokens[0] != "SELECT" or ";" in tokens:
        raise ValueError("verify.db.sql: only one SELECT statement is allowed")
    calls: list[str] = []  # function or group owning each open parenthesis
    for i, token in enumerate(tokens):
        if token == "(":
            calls.append(tokens[i - 1] if i else "")
        elif token == ")" and calls:
            calls.pop()
        # SUBSTRING(s FROM p FOR n) is standard syntax; any other FOR can lock rows.
        if token == "FOR" and calls and calls[-1] in {"SUBSTRING", "SUBSTR"}:
            continue
        if token in _FORBIDDEN:
            raise ValueError(f"verify.db.sql: {token} is not allowed in verification")
        if (
            i + 1 < len(tokens)
            and tokens[i + 1] == "("
            and (token[0].isalpha() or token[0] in '`"')
            and (token not in _FUNCTIONS | _GROUPS or (i and tokens[i - 1] == "."))
        ):
            raise ValueError(f"verify.db.sql: function {token} is not supported for verification")
