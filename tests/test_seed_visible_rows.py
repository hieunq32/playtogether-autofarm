import numpy as np

import main
from utils.detector import MatchResult


class FakeDetector:
    def __init__(self, matches):
        self.matches = matches

    def find_action_button(self, frame, action_name):
        return self.matches.get(action_name)


def test_visible_seed_rows_are_sorted_and_processed_rows_are_excluded():
    targets = [
        {"name": "pumpkin", "row_action": "seed_pumpkin_row"},
        {"name": "watermelon", "row_action": "seed_watermelon_row"},
        {"name": "coconut", "row_action": "seed_coconut_row"},
    ]
    matches = {
        "seed_pumpkin_row": MatchResult("pumpkin", 0.95, (20, 80), (20, 80)),
        "seed_watermelon_row": MatchResult("watermelon", 0.95, (20, 40), (20, 40)),
        "seed_coconut_row": MatchResult("coconut", 0.95, (20, 120), (20, 120)),
    }
    context = main.BotContext(
        config={"workflow": {"seed_purchase_targets": targets}},
        detector=FakeDetector(matches),
        capture=None,
        mouse=None,
    )

    visible = main.find_visible_seed_target_indices(context, np.zeros((10, 10, 3)), {1})

    assert visible == [0, 2]


def test_next_unprocessed_seed_target_is_kept_when_no_target_row_is_visible():
    targets = [
        {"name": "pumpkin"},
        {"name": "watermelon"},
        {"name": "coconut"},
    ]
    context = main.BotContext(
        config={"workflow": {"seed_purchase_targets": targets}},
        detector=None,
        capture=None,
        mouse=None,
    )
    context.seed_processed_target_indices.add(0)

    assert main.get_next_unprocessed_seed_target_index(context) == 1
    context.seed_processed_target_indices.update({1, 2})
    assert main.get_next_unprocessed_seed_target_index(context) is None


def test_visible_seed_rows_follow_configured_purchase_order():
    targets = [
        {"name": "pumpkin", "row_action": "seed_pumpkin_row"},
        {"name": "watermelon", "row_action": "seed_watermelon_row"},
        {"name": "coconut", "row_action": "seed_coconut_row"},
    ]
    matches = {
        "seed_pumpkin_row": MatchResult("pumpkin", 0.95, (20, 180), (20, 180)),
        "seed_watermelon_row": MatchResult("watermelon", 0.95, (20, 120), (20, 120)),
        "seed_coconut_row": MatchResult("coconut", 0.95, (20, 40), (20, 40)),
    }
    context = main.BotContext(
        config={"workflow": {"seed_purchase_targets": targets}},
        detector=FakeDetector(matches),
        capture=None,
        mouse=None,
    )

    visible = main.find_visible_seed_target_indices(context, np.zeros((10, 10, 3)))

    assert visible == [0, 1, 2]
