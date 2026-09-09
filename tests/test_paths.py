import pytest

from telegram_plugin.errors import UnsafePath
from telegram_plugin.paths import safe_output_path


def test_relative_escape_is_rejected(tmp_path):
    with pytest.raises(UnsafePath):
        safe_output_path("../outside.jsonl", root=tmp_path)


def test_absolute_path_outside_root_is_rejected(tmp_path):
    with pytest.raises(UnsafePath):
        safe_output_path("/etc/passwd", root=tmp_path)


def test_symlink_pointing_outside_is_rejected(tmp_path):
    (tmp_path / "link").symlink_to("/etc")
    with pytest.raises(UnsafePath):
        safe_output_path("link/passwd", root=tmp_path)


def test_path_inside_root_is_allowed(tmp_path):
    assert safe_output_path("a/b.jsonl", root=tmp_path) == tmp_path / "a" / "b.jsonl"


def test_absolute_path_inside_root_is_allowed(tmp_path):
    target = tmp_path / "a" / "b.jsonl"
    assert safe_output_path(str(target), root=tmp_path) == target


def test_existing_file_is_not_overwritten_silently(tmp_path):
    (tmp_path / "x.jsonl").write_text("old")
    with pytest.raises(UnsafePath):
        safe_output_path("x.jsonl", root=tmp_path, must_not_exist=True)


def test_existing_file_is_allowed_when_overwrite_is_intended(tmp_path):
    (tmp_path / "x.jsonl").write_text("old")
    assert safe_output_path("x.jsonl", root=tmp_path, must_not_exist=False) == tmp_path / "x.jsonl"


def test_root_itself_is_not_a_valid_target(tmp_path):
    with pytest.raises(UnsafePath):
        safe_output_path(str(tmp_path), root=tmp_path)


def test_output_dir_may_be_the_root_itself(tmp_path):
    from telegram_plugin.paths import safe_output_dir

    assert safe_output_dir(tmp_path, root=tmp_path) == tmp_path.resolve()


def test_output_dir_outside_root_is_rejected(tmp_path):
    import pytest

    from telegram_plugin.paths import safe_output_dir

    with pytest.raises(UnsafePath):
        safe_output_dir("/etc", root=tmp_path)


# A `..` in the FIRST component resolves against the existing root and was caught.
# A `..` after a component that does not exist yet was not: the resolver kept the
# non-existent tail literally, and Path.parents does not normalise `..`.
ESCAPES = [
    "x/../victim.txt",
    "x/../../../../etc/passwd",
    "a/b/../../victim.txt",
    "./x/./../../victim.txt",
    "x/../../..",
]


@pytest.mark.parametrize("payload", ESCAPES)
def test_dot_dot_after_a_missing_component_is_refused(payload, tmp_path):
    root = tmp_path / "downloads"
    root.mkdir()
    with pytest.raises(UnsafePath):
        safe_output_path(payload, root=root)


@pytest.mark.parametrize("payload", ESCAPES)
def test_the_same_escapes_are_refused_for_directories(payload, tmp_path):
    from telegram_plugin.paths import safe_output_dir

    root = tmp_path / "downloads"
    root.mkdir()
    with pytest.raises(UnsafePath):
        safe_output_dir(payload, root=root)


def test_nothing_outside_the_root_can_be_written_end_to_end(tmp_path):
    from telegram_plugin.jsonl import write_jsonl

    root = tmp_path / "downloads"
    root.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL")
    with pytest.raises(UnsafePath):
        write_jsonl(safe_output_path("x/../../victim.txt", root=root), [{"id": 1}])
    assert victim.read_text() == "ORIGINAL"


def test_a_nul_byte_is_refused_rather_than_reaching_open(tmp_path):
    with pytest.raises(UnsafePath):
        safe_output_path("bad\x00name.jsonl", root=tmp_path)


def test_a_symlinked_root_is_still_its_own_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert safe_output_path("a.jsonl", root=link) == real / "a.jsonl"
