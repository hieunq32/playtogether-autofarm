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
