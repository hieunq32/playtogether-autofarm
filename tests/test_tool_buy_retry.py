import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

import main


def test_premium_watering_tool_can_override_buy_button_retries():
    target = {
        "name": "watering_premium",
        "buy_button_retry_attempts": 2,
    }
    config = {"workflow": {"tool_buy_button_retry_attempts": 6}}

    assert main.get_tool_buy_button_retry_attempts(target, config) == 2


def test_tool_buy_retry_keeps_global_default_for_other_tools():
    target = {"name": "watering_basic"}
    config = {"workflow": {"tool_buy_button_retry_attempts": 6}}

    assert main.get_tool_buy_button_retry_attempts(target, config) == 6


def test_watering_basic_uses_two_buy_button_attempts_from_project_config():
    project_root = Path(__file__).resolve().parents[1]
    config = json.loads((project_root / "config.json").read_text(encoding="utf-8"))
    target = next(
        item
        for item in config["workflow"]["tool_purchase_targets"]
        if item["name"] == "watering_basic"
    )

    assert main.get_tool_buy_button_retry_attempts(target, config) == 2


def test_tool_row_status_is_matched_to_the_selected_row():
    class Detector:
        def find_action_button(self, frame, action_name):
            assert action_name == "tool_watering_super_row"
            return SimpleNamespace(click_point=(190, 190), score=0.95)

        def find_all_action_buttons(self, frame, action_name):
            matches = {
                "tool_row_in_stock_status": [
                    SimpleNamespace(click_point=(130, 100), score=0.98)
                ],
                "tool_row_sold_out_status": [
                    SimpleNamespace(click_point=(130, 202), score=0.91)
                ],
            }
            return matches.get(action_name, [])

    context = SimpleNamespace(
        config={"workflow": {"tool_row_status_tolerance_ratio": 0.1}},
        detector=Detector(),
    )

    status, match = main.get_tool_row_status_match(
        context,
        frame=SimpleNamespace(shape=(312, 528, 3)),
        target={"row_action": "tool_watering_super_row"},
    )

    assert status == "sold_out"
    assert match.click_point == (130, 202)


def test_sold_out_tool_is_skipped_without_trying_to_buy(monkeypatch):
    calls = []
    context = SimpleNamespace(
        config={
            "workflow": {"tool_purchase_targets": [{"display_name": "Vòi tưới"}]},
            "timing": {"button_retry_delay_seconds": [0, 0]},
        },
        tool_target_index=0,
        state_attempts=0,
    )
    monkeypatch.setattr(main, "capture_frame", lambda _: object())
    monkeypatch.setattr(main, "is_tool_shop_menu_visible", lambda *_: True)
    monkeypatch.setattr(main, "get_tool_row_status", lambda *_: "sold_out")
    monkeypatch.setattr(
        main,
        "click_seed_blue_button",
        lambda *args: calls.append("buy") or True,
    )
    monkeypatch.setattr(main, "advance_to_next_tool_target", lambda _: calls.append("skip"))
    monkeypatch.setattr(main, "sleep_random", lambda *_: None)

    main.handle_buy_tool_item(context)

    assert calls == ["skip"]


def test_unknown_tool_stock_is_retried_then_skipped_without_clicking(monkeypatch):
    calls = []
    context = SimpleNamespace(
        config={
            "workflow": {
                "tool_purchase_targets": [
                    {
                        "display_name": "Vòi tưới",
                        "buy_button_retry_attempts": 2,
                    }
                ]
            },
            "timing": {
                "button_retry_delay_seconds": [0, 0],
                "seed_buy_button_retry_delay_seconds": [0, 0],
            },
        },
        tool_target_index=0,
        state_attempts=0,
    )
    monkeypatch.setattr(main, "capture_frame", lambda _: object())
    monkeypatch.setattr(main, "is_tool_shop_menu_visible", lambda *_: True)
    monkeypatch.setattr(main, "get_tool_row_status", lambda *_: "unknown")
    monkeypatch.setattr(
        main,
        "click_seed_blue_button",
        lambda *args: calls.append("buy") or True,
    )
    monkeypatch.setattr(main, "advance_to_next_tool_target", lambda _: calls.append("skip"))
    monkeypatch.setattr(main, "sleep_random", lambda *_: None)

    main.handle_buy_tool_item(context)

    assert calls == ["skip"]


def test_in_stock_tool_advances_only_after_blue_button_is_detected(monkeypatch):
    calls = []
    context = SimpleNamespace(
        config={
            "workflow": {"tool_purchase_targets": [{"display_name": "Vòi tưới"}]},
            "timing": {
                "button_retry_delay_seconds": [0, 0],
                "post_navigation_wait_seconds": [0, 0],
            },
        },
        tool_target_index=0,
        state_attempts=0,
        state=main.BotState.BUY_TOOL_ITEM,
    )
    monkeypatch.setattr(main, "capture_frame", lambda _: object())
    monkeypatch.setattr(main, "is_tool_shop_menu_visible", lambda *_: True)
    monkeypatch.setattr(main, "get_tool_row_status", lambda *_: "available")
    monkeypatch.setattr(
        main,
        "click_seed_blue_button",
        lambda *_: calls.append("blue_button") or True,
    )
    monkeypatch.setattr(main, "sleep_random", lambda *_: None)

    main.handle_buy_tool_item(context)

    assert calls == ["blue_button"]
    assert context.state == main.BotState.CONFIRM_BUY_TOOL_ITEM


def test_in_stock_tool_without_detected_blue_button_is_skipped_safely(monkeypatch):
    buy_attempts = []
    skipped = []
    context = SimpleNamespace(
        config={
            "workflow": {
                "tool_purchase_targets": [
                    {
                        "display_name": "Vòi tưới",
                        "buy_button_retry_attempts": 2,
                    }
                ]
            },
            "timing": {
                "button_retry_delay_seconds": [0, 0],
                "seed_buy_button_retry_delay_seconds": [0, 0],
            },
        },
        tool_target_index=0,
        state_attempts=0,
    )
    monkeypatch.setattr(main, "capture_frame", lambda _: object())
    monkeypatch.setattr(main, "is_tool_shop_menu_visible", lambda *_: True)
    monkeypatch.setattr(main, "get_tool_row_status", lambda *_: "available")
    monkeypatch.setattr(
        main,
        "click_seed_blue_button",
        lambda *args: buy_attempts.append(args[1]) or False,
    )
    monkeypatch.setattr(main, "advance_to_next_tool_target", lambda _: skipped.append(True))
    monkeypatch.setattr(main, "sleep_random", lambda *_: None)

    main.handle_buy_tool_item(context)

    assert buy_attempts == ["tool_buy_price", "tool_buy_price"]
    assert skipped == [True]


def test_tool_purchase_actions_never_use_coordinate_fallback(monkeypatch):
    class Mouse:
        clicks = []

        def click_relative(self, window, point):
            self.clicks.append(point)

    context = SimpleNamespace(
        window=SimpleNamespace(width=528, height=312),
        reference_client_size=None,
        config={
            "navigation": {
                "allow_fallback_clicks": True,
                "buttons": {
                    "tool_buy_popup_submit": {
                        "fallback_click_ratio": [0.47, 0.76],
                        "force_allow_fallback": True,
                    }
                },
            }
        },
        mouse=Mouse(),
    )
    monkeypatch.setattr(main, "find_action_button_stable", lambda *_: (None, "template"))

    clicked = main.click_named_button(
        context,
        "tool_buy_popup_submit",
        frame=object(),
    )

    assert clicked is False
    assert context.mouse.clicks == []


def test_purple_gem_button_is_not_detected_as_a_tool_buy_button():
    context = SimpleNamespace(
        config={
            "navigation": {
                "buttons": {
                    "tool_buy_price": {
                        "search_region_ratio": [0.48, 0.74, 0.74, 0.98]
                    }
                }
            }
        }
    )
    frame = np.zeros((312, 528, 3), dtype=np.uint8)
    cv2.rectangle(frame, (300, 240), (400, 280), (210, 70, 220), -1)

    assert main.find_blue_button_point_in_action_region(
        context,
        frame,
        "tool_buy_price",
    ) is None


def test_coin_blue_button_is_detected_inside_the_tool_price_region():
    context = SimpleNamespace(
        config={
            "navigation": {
                "buttons": {
                    "tool_buy_price": {
                        "search_region_ratio": [0.48, 0.74, 0.74, 0.98]
                    }
                }
            }
        }
    )
    frame = np.zeros((312, 528, 3), dtype=np.uint8)
    cv2.rectangle(frame, (300, 240), (400, 280), (229, 182, 37), -1)

    assert main.find_blue_button_point_in_action_region(
        context,
        frame,
        "tool_buy_price",
    ) == (345, 260)


def test_tool_purchase_targets_are_limited_to_first_three_coin_items():
    project_root = Path(__file__).resolve().parents[1]
    config = json.loads((project_root / "config.json").read_text(encoding="utf-8"))

    assert [item["name"] for item in config["workflow"]["tool_purchase_targets"]] == [
        "watering_basic",
        "watering_premium",
        "watering_super",
    ]
    assert config["navigation"]["buttons"]["tool_buy_price"][
        "force_allow_fallback"
    ] is False
    assert config["navigation"]["buttons"]["tool_buy_popup_submit"][
        "force_allow_fallback"
    ] is False
