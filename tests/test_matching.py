from types import SimpleNamespace

import pytest

from telegram_plugin.client import DIALOG_SCAN_CAP, TelethonGateway
from telegram_plugin.matching import normalize_dialog_text, rank_dialogs


def _dialog(dialog_id, title, username=None):
    return {
        "id": dialog_id,
        "title": title,
        "type": "channel",
        "username": username,
        "unread": 0,
    }


def test_normalization_handles_unicode_case_at_sign_and_punctuation():
    assert normalize_dialog_text("  @ＲＥＬＥＡＳＥ—ЧаТ!  ") == "release чат"


def test_match_category_order():
    ranked = rank_dialogs(
        [
            _dialog(5, "Releaze Mobule"),
            _dialog(4, "Mobile status for Release"),
            _dialog(3, "Weekly Release Mobile Room"),
            _dialog(2, "Release Mobile Updates"),
            _dialog(1, "Release Mobile"),
        ],
        "release mobile",
        limit=10,
    )

    assert [row["id"] for row in ranked] == [1, 2, 3, 4, 5]
    assert [row["score"] for row in ranked[:4]] == [100, 90, 80, 70]
    assert 40 <= ranked[4]["score"] <= 69
    assert [row["matched_by"] for row in ranked[:4]] == [
        "title_exact",
        "title_prefix",
        "title_substring",
        "title_all_tokens",
    ]


def test_exact_username_ignores_leading_at_and_punctuation():
    ranked = rank_dialogs(
        [_dialog(1, "Release Chat", "mobile_release")],
        "@MOBILE-RELEASE",
        limit=5,
    )

    assert ranked[0]["score"] == 100
    assert ranked[0]["matched_by"] == "username_exact"


def test_username_fragment_beats_title_typo():
    ranked = rank_dialogs(
        [
            _dialog(1, "Release Chat", "mobile_release"),
            _dialog(2, "Mobile Relese"),
        ],
        "@mobile_rel",
        limit=5,
    )

    assert ranked[0]["id"] == 1
    assert ranked[0]["matched_by"] == "username_prefix"


def test_one_typo_matches_but_low_similarity_is_excluded():
    ranked = rank_dialogs(
        [_dialog(1, "Releaze"), _dialog(2, "Kitchen")],
        "release",
        limit=5,
    )

    assert [row["id"] for row in ranked] == [1]
    assert 40 <= ranked[0]["score"] <= 69
    assert ranked[0]["matched_by"] == "title_typo"


def test_ranking_is_deterministic():
    dialogs = [
        _dialog(9, "Release room"),
        _dialog(4, "release room"),
        _dialog(2, "Another Release"),
    ]

    expected = [4, 9, 2]
    for source in (dialogs, list(reversed(dialogs)), dialogs[1:] + dialogs[:1]):
        assert [row["id"] for row in rank_dialogs(source, "release", limit=10)] == expected


def test_limit_bounds_candidates():
    dialogs = [_dialog(index, f"Release {index}") for index in range(1, 8)]

    assert len(rank_dialogs(dialogs, "release", limit=3)) == 3


@pytest.mark.parametrize("query", ["", "   ", "@"])
def test_empty_normalized_query_is_rejected(query):
    with pytest.raises(ValueError, match="query"):
        rank_dialogs([], query, limit=5)


async def test_dialog_scan_marks_truncation_at_the_cap():
    class Client:
        async def iter_dialogs(self):
            for index in range(DIALOG_SCAN_CAP + 1):
                yield SimpleNamespace(
                    id=-(index + 1),
                    name=f"Chat {index}",
                    entity=SimpleNamespace(username=None),
                    unread_count=0,
                    is_user=False,
                    is_group=False,
                    is_channel=True,
                )

    batch = await TelethonGateway._scan_dialogs(
        None,
        Client(),
        query=None,
        limit=DIALOG_SCAN_CAP,
    )

    assert len(batch.rows) == DIALOG_SCAN_CAP
    assert batch.scanned == DIALOG_SCAN_CAP
    assert batch.scan_truncated is True
