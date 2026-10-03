import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import main


def load_project_config():
    return json.loads(
        (Path(__file__).resolve().parents[1] / "config.json").read_text(
            encoding="utf-8"
        )
    )


def test_sell_entry_rejects_an_order_icon_match_and_red_fallback(monkeypatch):
    class Detector:
        def find_action_button(self, frame, action_name):
            return SimpleNamespace(click_point=(445, 105), score=0.99)

    class Mouse:
        def __init__(self):
            self.clicks = []

        def click_relative(self, window, point):
            self.clicks.append(point)

    config = load_project_config()
    config["matching"]["button_extra_frame_retries"] = 0
    config["navigation"]["buttons"]["sell_entry"]["search_region_ratio"] = [
        0.72,
        0.18,
        0.94,
        0.48,
    ]
    mouse = Mouse()
    context = SimpleNamespace(
        config=config,
        detector=Detector(),
        mouse=mouse,
        window=SimpleNamespace(width=528, height=312),
        reference_client_size=(528, 312),
    )
    frame = np.zeros((312, 528, 3), dtype=np.uint8)
    cv2.rectangle(frame, (434, 94), (456, 116), (0, 0, 255), -1)
    monkeypatch.setattr(main, "sleep_random", lambda _: None)

    clicked = main.click_sell_entry_button(context, frame)

    assert clicked is False
    assert mouse.clicks == []


def test_sell_auto_select_runs_before_already_visible_blue_button(monkeypatch):
    config = load_project_config()
    context = SimpleNamespace(
        config=config,
        state_attempts=0,
        sell_auto_select_cycles_completed=0,
        sell_auto_select_pending=False,
    )
    actions = []
    monkeypatch.setattr(main, "capture_frame", lambda current_context: object())
    monkeypatch.setattr(main, "sync_state_from_visible_screen", lambda *_: False)
    monkeypatch.setattr(main, "is_sell_bottom_submit_visible", lambda *_: True)
    monkeypatch.setattr(main, "click_named_button", lambda _c, name, _f: actions.append(name) or True)
    monkeypatch.setattr(
        main,
        "click_sell_bottom_submit_and_route",
        lambda *_: actions.append("sell_bottom_submit") or True,
    )
    monkeypatch.setattr(main, "sleep_random", lambda _: None)

    main.handle_sell_auto_select(context)

    assert actions == ["sell_auto_select", "sell_bottom_submit"]


def test_clicking_blue_button_waits_for_sale_success_before_completing_cycle(monkeypatch):
    context = SimpleNamespace(
        config=load_project_config(),
        sell_auto_select_pending=True,
        sell_auto_select_cycles_completed=0,
    )
    monkeypatch.setattr(main, "click_named_button", lambda *_: True)
    monkeypatch.setattr(main, "sleep_random", lambda _: None)
    monkeypatch.setattr(main, "capture_frame", lambda _: object())
    monkeypatch.setattr(main, "sync_state_from_visible_screen", lambda *_: True)

    assert main.click_sell_bottom_submit_and_route(context, object()) is True
    assert context.sell_auto_select_cycles_completed == 0
    assert context.sell_auto_select_pending is True


def test_sell_success_confirmation_completes_cycle_and_restarts_auto_select(monkeypatch):
    context = SimpleNamespace(
        config=load_project_config(),
        state=main.BotState.SELL_SUCCESS_OK,
        state_attempts=0,
        sell_auto_select_pending=True,
        sell_auto_select_cycles_completed=0,
    )
    monkeypatch.setattr(main, "capture_frame", lambda _: object())
    monkeypatch.setattr(main, "sync_state_from_visible_screen", lambda *_: False)
    monkeypatch.setattr(main, "click_named_button", lambda *_: True)
    monkeypatch.setattr(main, "is_sell_screen_close_visible", lambda *_: True)
    monkeypatch.setattr(main, "is_sell_auto_select_visible", lambda *_: True)
    monkeypatch.setattr(main, "is_sell_bottom_submit_visible", lambda *_: True)
    monkeypatch.setattr(main, "is_sell_popup_submit_visible", lambda *_: False)
    monkeypatch.setattr(main, "is_sell_final_confirm_visible", lambda *_: False)
    monkeypatch.setattr(main, "is_sell_success_ok_visible", lambda *_: False)
    monkeypatch.setattr(main, "sleep_random", lambda _: None)

    main.handle_sell_success_ok(context)

    assert context.sell_auto_select_cycles_completed == 1
    assert context.sell_auto_select_pending is False
    assert context.state == main.BotState.SELL_AUTO_SELECT


def test_missing_sale_confirmation_popup_ends_sell_flow_as_out_of_stock(monkeypatch):
    context = SimpleNamespace(
        config=load_project_config(),
        state=main.BotState.SELL_SUBMIT_SELECTION,
        state_attempts=0,
        sell_auto_select_pending=True,
        sell_auto_select_cycles_completed=0,
    )
    monkeypatch.setattr(main, "click_named_button", lambda *_: True)
    monkeypatch.setattr(main, "sleep_random", lambda _: None)
    monkeypatch.setattr(main, "capture_frame", lambda _: object())
    monkeypatch.setattr(main, "sync_state_from_visible_screen", lambda *_: False)
    monkeypatch.setattr(main, "is_sell_screen_close_visible", lambda *_: True)
    monkeypatch.setattr(main, "is_sell_popup_submit_visible", lambda *_: False)
    monkeypatch.setattr(main, "is_sell_final_confirm_visible", lambda *_: False)
    monkeypatch.setattr(main, "is_sell_success_ok_visible", lambda *_: False)

    assert main.click_sell_bottom_submit_and_route(context, object()) is True
    assert context.sell_auto_select_cycles_completed == 0
    assert context.sell_auto_select_pending is False
    assert context.state == main.BotState.SELL_CLOSE_SCREEN


@pytest.mark.parametrize(
    ("completed_cycles", "expected_state"),
    [
        (0, main.BotState.SELL_AUTO_SELECT),
        (1, main.BotState.SELL_CLOSE_SCREEN),
    ],
)
def test_sell_success_repeats_once_then_closes_after_two_cycles(
    monkeypatch,
    completed_cycles,
    expected_state,
):
    context = main.BotContext(
        config=load_project_config(),
        detector=None,
        capture=None,
        mouse=None,
    )
    context.state = main.BotState.SELL_SUCCESS_OK
    context.sell_auto_select_cycles_completed = completed_cycles
    context.sell_auto_select_pending = True
    monkeypatch.setattr(main, "is_escape_pressed", iter((False, True)).__next__)
    monkeypatch.setattr(main, "refresh_game_window", lambda _: True)
    monkeypatch.setattr(main, "bootstrap_visible_flow_on_start", lambda _: False)
    monkeypatch.setattr(main, "capture_frame", lambda _: object())
    monkeypatch.setattr(main, "sync_state_from_visible_screen", lambda *_: False)
    monkeypatch.setattr(main, "click_named_button", lambda *_: True)
    monkeypatch.setattr(main, "is_sell_screen_close_visible", lambda *_: True)
    monkeypatch.setattr(main, "is_sell_auto_select_visible", lambda *_: True)
    monkeypatch.setattr(main, "is_sell_bottom_submit_visible", lambda *_: True)
    monkeypatch.setattr(main, "is_sell_popup_submit_visible", lambda *_: False)
    monkeypatch.setattr(main, "is_sell_final_confirm_visible", lambda *_: False)
    monkeypatch.setattr(main, "is_sell_success_ok_visible", lambda *_: False)
    monkeypatch.setattr(main, "sleep_random", lambda _: None)

    main.run_state_machine(context)

    assert context.state == expected_state


def test_completed_sell_and_tool_flows_restart_harvesting(monkeypatch):
    context = main.BotContext(
        config=load_project_config(),
        detector=None,
        capture=None,
        mouse=None,
    )
    main.finish_sell_flow(context)
    assert context.state == main.BotState.OPEN_TOOL_SHOP_ENTRY

    main.finish_tool_purchase_flow(context)
    assert context.state == main.BotState.SESSION_DONE
    assert context.restart_harvest_immediately is True

    monkeypatch.setattr(main, "is_escape_pressed", iter((False, True)).__next__)
    monkeypatch.setattr(main, "refresh_game_window", lambda _: True)
    monkeypatch.setattr(main, "bootstrap_visible_flow_on_start", lambda _: False)
    monkeypatch.setattr(main, "sleep_random", lambda _: None)

    main.run_state_machine(context)

    assert context.state == main.BotState.OPEN_HARVEST_ENTRY
