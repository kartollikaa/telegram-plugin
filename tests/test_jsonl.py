import json

from telegram_plugin.jsonl import write_jsonl


def test_writes_one_line_per_row_and_reports_range(tmp_path):
    out = tmp_path / "nested" / "dump.jsonl"
    result = write_jsonl(out, [{"id": 3, "text": "a"}, {"id": 9, "text": "b"}])
    assert result == {"path": str(out), "lines": 2, "first_id": 3, "last_id": 9}
    assert [json.loads(line)["id"] for line in out.read_text().splitlines()] == [3, 9]


def test_result_does_not_carry_the_rows(tmp_path):
    result = write_jsonl(tmp_path / "d.jsonl", [{"id": 1, "text": "secret"}])
    assert "items" not in result
    assert "secret" not in json.dumps(result)


def test_non_ascii_is_kept_readable(tmp_path):
    out = tmp_path / "d.jsonl"
    write_jsonl(out, [{"id": 1, "text": "привет"}])
    assert "привет" in out.read_text(encoding="utf-8")


def test_empty_rows(tmp_path):
    result = write_jsonl(tmp_path / "d.jsonl", [])
    assert result["lines"] == 0
    assert result["first_id"] is None
    assert result["last_id"] is None
