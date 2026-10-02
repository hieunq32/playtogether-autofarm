import main
from utils.detector import HarvestRowMatch


def _row(name: str, y: int) -> HarvestRowMatch:
    return HarvestRowMatch(
        fruit_name=name,
        label_point=(20, y),
        button_point=(200, y),
        label_score=0.95,
        button_score=0.95,
    )


def test_select_harvest_batch_limits_clicks_to_three_rows():
    rows = [_row("durian", 40), _row("coconut", 80), _row("bean", 120), _row("starfruit", 160)]

    selected = main.select_harvest_batch(rows, 3)

    assert selected == rows[:3]


def test_harvest_lock_popup_uses_two_fast_scrolls_by_default():
    config = {"workflow": {}}

    assert main.get_harvest_lock_scroll_count(config) == 2
