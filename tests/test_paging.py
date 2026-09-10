from telegram_plugin.paging import paginate


def test_page_is_capped_and_reports_more():
    page = paginate(list(range(1, 11)), limit=4)
    assert page.items == [1, 2, 3, 4]
    assert page.has_more is True
    assert page.next_cursor == 4


def test_last_page_has_no_cursor():
    page = paginate([1, 2], limit=4)
    assert page.items == [1, 2]
    assert page.has_more is False
    assert page.next_cursor is None


def test_second_page_does_not_repeat_ids():
    all_ids = list(range(1, 11))
    first = paginate(all_ids, limit=4)
    second = paginate([i for i in all_ids if i > first.next_cursor], limit=4)
    assert set(first.items) & set(second.items) == set()
    assert second.items == [5, 6, 7, 8]


def test_empty_input():
    page = paginate([], limit=4)
    assert page.items == []
    assert page.has_more is False
    assert page.next_cursor is None
