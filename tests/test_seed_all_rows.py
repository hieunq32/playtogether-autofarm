import json
from pathlib import Path

import main
import cv2
import numpy as np
from types import SimpleNamespace


def test_all_seed_mode_uses_five_visible_row_slots():
    config = {
        "workflow": {
            "seed_shop_all_rows": {
                "row_click_x_ratio": 0.24,
                "row_click_y_ratios": [0.32, 0.47, 0.62, 0.76, 0.89],
            }
        }
    }

    assert main.get_seed_shop_row_ratios(config) == (
        0.24,
        [0.32, 0.47, 0.62, 0.76, 0.89],
    )


def test_all_seed_mode_is_explicitly_enabled():
    context_config = {"workflow": {"seed_purchase_all_rows": True}}

    assert main.seed_purchase_all_rows_enabled(context_config) is True


def test_seed_row_signature_is_stable_and_changes_for_a_different_row():
    frame_a = np.zeros((312, 528, 3), dtype=np.uint8)
    frame_b = frame_a.copy()
    frame_a[80:100, 80:210] = (40, 120, 220)
    frame_b[80:100, 80:210] = (40, 220, 120)

    signature_a = main.get_seed_shop_row_signature(frame_a, 0, {"workflow": {}})
    same_signature = main.get_seed_shop_row_signature(frame_a, 0, {"workflow": {}})
    signature_b = main.get_seed_shop_row_signature(frame_b, 0, {"workflow": {}})

    assert signature_a == same_signature
    assert signature_a != signature_b


def test_seed_row_template_skips_same_row_after_it_moves_to_another_slot():
    config = {
        "workflow": {
            "seed_shop_row_identity_match_threshold": 0.86,
        }
    }
    first_frame = np.full((312, 528, 3), 230, dtype=np.uint8)
    next_frame = first_frame.copy()
    cv2.circle(first_frame, (80, 230), 12, (20, 120, 220), -1)
    cv2.putText(
        first_frame,
        "POTATO",
        (105, 234),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        (20, 20, 20),
        1,
        cv2.LINE_AA,
    )
    cv2.circle(next_frame, (80, 100), 12, (20, 120, 220), -1)
    cv2.putText(
        next_frame,
        "POTATO",
        (105, 104),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        (20, 20, 20),
        1,
        cv2.LINE_AA,
    )
    cv2.circle(next_frame, (80, 193), 12, (20, 220, 80), -1)
    cv2.putText(
        next_frame,
        "CORN",
        (105, 197),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        (20, 20, 20),
        1,
        cv2.LINE_AA,
    )

    context = type("Context", (), {})()
    context.config = config
    context.seed_shop_processed_row_templates = [
        main.get_seed_shop_row_template(first_frame, 3, config)
    ]

    moved_row = main.get_seed_shop_row_template(next_frame, 0, config)
    new_row = main.get_seed_shop_row_template(next_frame, 2, config)

    assert main.seed_shop_row_template_is_processed(context, next_frame, 0, moved_row)
    assert not main.seed_shop_row_template_is_processed(context, next_frame, 2, new_row)


def test_seed_row_status_is_classified_by_the_current_row():
    class Detector:
        def find_all_action_buttons(self, frame, action_name):
            matches = {
                "seed_row_in_stock_status": [
                    SimpleNamespace(click_point=(120, 100), score=0.95)
                ],
                "seed_row_sold_out_status": [
                    SimpleNamespace(click_point=(120, 193), score=0.96)
                ],
            }
            return matches.get(action_name, [])

    context = SimpleNamespace(
        config={
            "workflow": {
                "seed_shop_all_rows": {
                    "row_click_y_ratios": [0.32, 0.47, 0.62],
                }
            }
        },
        window=SimpleNamespace(width=528, height=312),
        detector=Detector(),
    )
    frame = np.zeros((312, 528, 3), dtype=np.uint8)

    assert main.get_seed_shop_row_status(context, frame, 0) == "available"
    assert main.get_seed_shop_row_status(context, frame, 2) == "sold_out"


def test_seed_quantity_plus_clicks_are_configurable():
    config = {
        "workflow": {
            "seed_quantity_plus_clicks": 4,
            "seed_quantity_plus_click_interval_seconds": [0.04, 0.08],
        }
    }

    assert main.get_seed_quantity_plus_clicks(config) == 4
    assert main.get_seed_quantity_plus_delay(config) == [0.04, 0.08]


def test_seed_shop_initial_scan_scrolls_down_three_times(monkeypatch):
    class Mouse:
        def __init__(self):
            self.scroll_calls = []

        def scroll_relative(self, window, point, wheel_delta, distance_pixels):
            self.scroll_calls.append((point, wheel_delta, distance_pixels))

    window = SimpleNamespace(width=528, height=312)
    mouse = Mouse()
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "config.json").read_text(
            encoding="utf-8"
        )
    )
    context = SimpleNamespace(
        config=config,
        window=window,
        mouse=mouse,
        seed_shop_position_reset_done=False,
        seed_shop_scroll_attempts=0,
        seed_shop_slot_index=0,
        seed_shop_last_page_signature=None,
        state_attempts=0,
    )
    monkeypatch.setattr(main, "sleep_random", lambda _: None)

    assert main.scroll_seed_shop_initially(context) is True
    assert mouse.scroll_calls == [((170, 237), -180, 500)] * 3
    assert context.seed_shop_position_reset_done is True
    assert context.seed_shop_scroll_attempts == 3


def test_seed_terminal_row_detection_uses_sugar_apple_template():
    class Detector:
        requested_action = None

        def find_all_action_buttons(self, frame, action_name):
            self.requested_action = action_name
            return [SimpleNamespace(score=0.94, click_point=(141, 264))]

    detector = Detector()
    context = SimpleNamespace(
        config={
            "workflow": {
                "seed_shop_all_rows": {
                    "row_click_y_ratios": [0.32, 0.47, 0.62, 0.76, 0.89]
                },
                "seed_terminal_row_action": "seed_sugar_apple_row",
            }
        },
        window=SimpleNamespace(width=528, height=312),
        detector=detector,
    )

    assert main.seed_shop_row_is_terminal(context, np.zeros((312, 528, 3)), 4)
    assert detector.requested_action == "seed_sugar_apple_row"


def test_buying_terminal_sugar_apple_row_closes_seed_shop():
    context = SimpleNamespace(
        config={"workflow": {"seed_purchase_all_rows": True}},
        seed_shop_active_signature=None,
        seed_shop_active_row_signature=None,
        seed_shop_active_row_template=None,
        seed_shop_pending_signature=None,
        seed_shop_pending_row_signature=None,
        seed_shop_pending_row_template=None,
        seed_shop_finish_after_active_row=True,
        seed_quantity_plus_clicks_done=3,
        seed_shop_processed_signatures=set(),
        seed_shop_processed_row_templates=[],
        seed_shop_slot_index=4,
        state_attempts=1,
        state=main.BotState.SEARCH_PUMPKIN_SEED,
    )

    main.advance_to_next_seed_target(context)

    assert context.state == main.BotState.CLOSE_SEED_SHOP
    assert context.seed_shop_finish_after_active_row is False
    assert context.seed_quantity_plus_clicks_done == 0


def test_seed_row_status_match_prefers_nearest_row_over_higher_score_neighbor():
    class Detector:
        def find_all_action_buttons(self, frame, action_name):
            if action_name == "seed_row_in_stock_status":
                return [SimpleNamespace(score=0.90, click_point=(123, 132))]
            if action_name == "seed_row_sold_out_status":
                return [SimpleNamespace(score=0.99, click_point=(123, 176))]
            return []

    context = SimpleNamespace(
        config={
            "workflow": {
                "seed_shop_all_rows": {
                    "row_click_y_ratios": [0.32, 0.47, 0.62, 0.76, 0.89]
                },
                "seed_shop_row_status_tolerance_ratio": 0.10,
            }
        },
        window=SimpleNamespace(width=528, height=312),
        detector=Detector(),
    )

    status, match = main.get_seed_shop_row_status_match(
        context,
        np.zeros((312, 528, 3)),
        1,
    )

    assert status == "available"
    assert match.click_point == (123, 132)


def test_seed_row_selection_uses_detected_status_y_coordinate():
    context = SimpleNamespace(
        config={
            "workflow": {
                "seed_shop_all_rows": {
                    "row_click_x_ratio": 0.24,
                    "row_click_y_ratios": [0.32, 0.47, 0.62, 0.76, 0.89],
                }
            }
        },
        window=SimpleNamespace(width=528, height=312),
    )

    assert main.get_seed_shop_row_selection_point(
        context,
        0,
        SimpleNamespace(click_point=(123, 91)),
    ) == (127, 91)


def test_seed_action_clicks_use_faster_seed_specific_delay():
    config = {
        "timing": {
            "click_delay_seconds": [0.18, 0.35],
            "seed_click_delay_seconds": [0.08, 0.15],
        }
    }

    assert main.get_click_delay_for_action(config, "seed_buy_price") == [0.08, 0.15]
    assert main.get_click_delay_for_action(config, "sell_entry") == [0.18, 0.35]


def test_seed_shop_npc_trigger_search_region_includes_shifted_icon():
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "config.json").read_text(
            encoding="utf-8"
        )
    )
    detector = main.TemplateDetector(config)
    template = detector.action_templates["seed_shop_npc_trigger"][0]
    frame = np.zeros((312, 528, 3), dtype=np.uint8)
    frame[65:104, 226:270] = cv2.cvtColor(template.image, cv2.COLOR_GRAY2BGR)

    match = detector.find_action_button(frame, "seed_shop_npc_trigger")

    assert match is not None
    assert match.click_point == (248, 85)


def test_sold_out_status_uses_a_clean_text_candidate():
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "config.json").read_text(
            encoding="utf-8"
        )
    )
    sold_out = config["navigation"]["buttons"]["seed_row_sold_out_status"]

    assert "templates/ui/seed_row_sold_out_status_clean.png" in sold_out[
        "template_candidates"
    ]
