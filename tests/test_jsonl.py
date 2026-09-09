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


def test_an_existing_file_is_never_clobbered(tmp_path):
    import pytest

    from telegram_plugin.errors import UnsafePath

    target = tmp_path / "d.jsonl"
    target.write_text("ORIGINAL")
    with pytest.raises(UnsafePath):
        write_jsonl(target, [{"id": 1}])
    assert target.read_text() == "ORIGINAL"


def test_a_symlink_at_the_target_is_never_followed(tmp_path):
    import pytest

    from telegram_plugin.errors import UnsafePath

    outside = tmp_path / "outside.txt"
    outside.write_text("ORIGINAL")
    link = tmp_path / "link.jsonl"
    link.symlink_to(outside)
    with pytest.raises(UnsafePath):
        write_jsonl(link, [{"id": 1}])
    assert outside.read_text() == "ORIGINAL"
