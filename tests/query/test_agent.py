"""Regression tests for the SQL-as-prose safety net.

This is a pure code utility, not a model-behavior test: it's the deterministic
catch that runs regardless of what the model does, added after the model
repeatedly ended its turn by printing a SQL query instead of calling run_sql."""

from association.query.agent import _extract_unrun_sql


def test_extract_sql_from_fenced_sql_block():
    text = "Here is the corrected query:\n```sql\nSELECT * FROM players\n```"
    assert _extract_unrun_sql(text) == "SELECT * FROM players"


def test_extract_sql_from_bare_fence_no_language_tag():
    text = "```\nWITH x AS (SELECT 1) SELECT * FROM x\n```"
    assert _extract_unrun_sql(text) == "WITH x AS (SELECT 1) SELECT * FROM x"


def test_extract_sql_from_unfenced_bare_query():
    text = "SELECT display_name FROM players"
    assert _extract_unrun_sql(text) == "SELECT display_name FROM players"


def test_extract_sql_returns_none_for_plain_prose_answer():
    text = "Domantas Sabonis had the most triple-doubles with 26."
    assert _extract_unrun_sql(text) is None


def test_extract_sql_returns_none_for_non_sql_fenced_code():
    text = "```python\nprint('hi')\n```"
    assert _extract_unrun_sql(text) is None


def test_extract_sql_handles_empty_and_none():
    assert _extract_unrun_sql("") is None
    assert _extract_unrun_sql(None) is None


def test_extract_sql_picks_first_valid_sql_block_among_several():
    text = "```text\nnot sql\n```\n```sql\nSELECT 1\n```"
    assert _extract_unrun_sql(text) == "SELECT 1"
