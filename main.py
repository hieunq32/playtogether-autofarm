import time
import traceback
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from utils.config import load_config
from utils.adb import BlueStacksAdb
from utils.detector import HarvestRowMatch, TemplateDetector
from utils.geometry import (
    clamp_point,
    get_reference_client_size,
    resolve_click_offset,
    resolve_click_position,
    resolve_point,
)
from utils.hotkey import is_escape_pressed
from utils.input_controller import MouseController
from utils.logger import log
from utils.screen import ScreenCapture
from utils.timing import random_in_range, sleep_random
from utils.window import GameWindow, activate_window, find_bluestacks_window, is_window_foreground


class BotState(Enum):
    WAIT_FOR_CHECK = auto()
    OPEN_HOME = auto()
    OPEN_AVAILABLE_TO_HARVEST = auto()
    OPEN_HARVEST_POPUP = auto()
    SEARCH_TARGET_ROWS = auto()
    HARVEST_ROW = auto()
    SCROLL_LIST = auto()
    BAG_FULL = auto()
    OPEN_SEED_SHOP_ENTRY = auto()
    OPEN_SEED_SHOP_NPC_MENU = auto()
    OPEN_SEED_SHOP_BUY_OPTION = auto()
    SEARCH_PUMPKIN_SEED = auto()
    SELECT_PUMPKIN_SEED = auto()
    BUY_PUMPKIN_SEED = auto()
    CONFIRM_BUY_PUMPKIN_SEED = auto()
    CLOSE_SEED_SHOP = auto()
    LEAVE_SEED_SHOP_MENU = auto()
    DISMISS_SEED_SHOP_END_DIALOG = auto()
    OPEN_SELL_ENTRY = auto()
    OPEN_SELL_CART = auto()
    ADVANCE_SELL_NPC_DIALOG = auto()
    OPEN_SELL_PRODUCE_OPTION = auto()
    SELL_AUTO_SELECT = auto()
    SELL_SUBMIT_SELECTION = auto()
    SELL_CONFIRM_POPUP_SUBMIT = auto()
    SELL_FINAL_CONFIRM = auto()
    SELL_SUCCESS_OK = auto()
    SELL_CLOSE_SCREEN = auto()
    SELL_DISMISS_END_DIALOG = auto()
    SESSION_DONE = auto()


@dataclass
class BotContext:
    config: dict
    detector: TemplateDetector
    capture: ScreenCapture
    mouse: MouseController
    reference_client_size: Optional[tuple[int, int]] = None
    state: BotState = BotState.WAIT_FOR_CHECK
    window: Optional[GameWindow] = None
    active_row: Optional[HarvestRowMatch] = None
    last_window_refresh: float = 0.0
    last_window_activation: float = 0.0
    next_check_time: float = 0.0
    state_attempts: int = 0
    scroll_attempts: int = 0
    no_match_search_attempts: int = 0
    session_harvest_count: int = 0
    harvest_navigation_recoveries: int = 0
    restart_harvest_immediately: bool = False
    sell_flow_active: bool = False
    seed_flow_active: bool = False
    seed_target_index: int = 0
    startup_flow_bootstrapped: bool = False


def set_state(context: BotContext, new_state: BotState) -> None:
    if context.state != new_state:
        log(f"State -> {new_state.name}")
        context.state = new_state
        context.state_attempts = 0


def refresh_game_window(context: BotContext) -> bool:
    if context.capture.background_enabled and context.mouse.background_enabled:
        if context.window is None:
            width, height = context.reference_client_size or (528, 312)
            context.window = GameWindow(
                hwnd=0,
                title="BlueStacks ADB background",
                left=0,
                top=0,
                width=width,
                height=height,
            )
            log(
                "Da gan BlueStacks qua ADB background voi khung logic "
                f"{width}x{height}. Co the de cua so khac che BlueStacks."
            )
        return True

    search_interval = context.config["window"].get("search_interval_seconds", 3.0)
    should_refresh = context.window is None or (
        time.monotonic() - context.last_window_refresh >= search_interval
    )

    if not should_refresh:
        return True

    window = find_bluestacks_window(context.config["window"]["title_keywords"])
    context.last_window_refresh = time.monotonic()

    if window is None:
        log("Khong tim thay cua so BlueStacks. Dang cho...")
        context.window = None
        return False

    if context.window is None or context.window.bounds != window.bounds:
        log(
            "Da gan cua so BlueStacks: "
            f"'{window.title}' tai ({window.left}, {window.top}) "
            f"kich thuoc {window.width}x{window.height}"
        )

    context.window = window
    return True


def capture_frame(context: BotContext):
    if context.window is None:
        raise RuntimeError("Chua co cua so game de chup.")
    ensure_game_window_active(context)

    retries = int(context.config["window"].get("blank_frame_retry_count", 2))
    retry_delay = float(context.config["window"].get("blank_frame_retry_delay_seconds", 0.2))

    for attempt in range(retries + 1):
        frame = context.capture.grab(context.window)
        if not is_likely_blank_frame(frame):
            return frame

        if attempt < retries:
            time.sleep(retry_delay)
            ensure_game_window_active(context)

    return frame


def ensure_game_window_active(context: BotContext) -> None:
    if context.window is None:
        return
    if context.capture.background_enabled and context.mouse.background_enabled:
        return

    window_config = context.config["window"]
    if not window_config.get("activate_before_capture", True):
        return

    cooldown = float(window_config.get("activate_cooldown_seconds", 0.8))
    now = time.monotonic()
    if is_window_foreground(context.window) and (
        now - context.last_window_activation < cooldown
    ):
        return

    activate_window(context.window)
    context.last_window_activation = now
    settle_delay = float(window_config.get("activation_settle_seconds", 0.15))
    if settle_delay > 0:
        time.sleep(settle_delay)


def is_likely_blank_frame(frame) -> bool:
    return float(frame.mean()) >= 245.0 and float(frame.std()) <= 8.0


def get_navigation_config(context: BotContext, action_name: str) -> dict:
    return context.config["navigation"]["buttons"].get(action_name, {})


def get_scroll_hover_point(context: BotContext) -> tuple[int, int]:
    if context.window is None:
        raise RuntimeError("Chua co cua so game de scroll.")

    scroll_config = context.config["navigation"]["scroll"]
    hover_ratio = scroll_config.get("hover_point_ratio", [0.8, 0.5])
    point_config = {
        "x_ratio": float(hover_ratio[0]),
        "y_ratio": float(hover_ratio[1]),
    }
    return resolve_point(
        point_config,
        (context.window.width, context.window.height),
        context.reference_client_size,
    )


def click_named_button(context: BotContext, action_name: str, frame=None) -> bool:
    if context.window is None:
        return False

    if frame is None:
        frame = capture_frame(context)

    button_match = context.detector.find_action_button(frame, action_name)
    button_config = get_navigation_config(context, action_name)

    if button_match is not None:
        click_point = button_match.click_point
        source = "template"
    else:
        allow_fallback = context.config["navigation"].get("allow_fallback_clicks", False)
        if button_config.get("force_allow_fallback", False):
            allow_fallback = True

        if not allow_fallback:
            log(f"Bo qua fallback cho '{action_name}' vi allow_fallback_clicks=false.")
            return False
        click_point = resolve_click_position(
            button_config,
            (context.window.width, context.window.height),
            context.reference_client_size,
        )
        if click_point is None:
            log(f"Khong tim thay button '{action_name}' va khong co fallback.")
            return False
        source = "fallback"

    offset_x, offset_y = resolve_click_offset(
        button_config,
        (context.window.width, context.window.height),
        context.reference_client_size,
    )
    if offset_x or offset_y:
        click_point = clamp_point(
            (click_point[0] + offset_x, click_point[1] + offset_y),
            (context.window.width, context.window.height),
        )
        source = f"{source}+offset"

    context.mouse.click_relative(context.window, click_point)
    log(f"Clicked '{action_name}' bang {source} tai {click_point}.")
    sleep_random(context.config["timing"]["click_delay_seconds"])
    return True


def resolve_action_search_region(context: BotContext, action_name: str, frame) -> tuple[int, int, int, int]:
    button_config = get_navigation_config(context, action_name)
    region_ratio = button_config.get("search_region_ratio")
    height, width = frame.shape[:2]
    if not isinstance(region_ratio, list) or len(region_ratio) != 4:
        return 0, 0, width, height

    left = max(0, min(width - 1, round(float(region_ratio[0]) * width)))
    top = max(0, min(height - 1, round(float(region_ratio[1]) * height)))
    right = max(left + 1, min(width, round(float(region_ratio[2]) * width)))
    bottom = max(top + 1, min(height, round(float(region_ratio[3]) * height)))
    return left, top, right, bottom


def find_blue_button_point_in_action_region(
    context: BotContext,
    frame,
    action_name: str,
) -> Optional[tuple[int, int]]:
    left, top, right, bottom = resolve_action_search_region(context, action_name, frame)
    roi = frame[top:bottom, left:right]
    if roi.size == 0:
        return None

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    # Nut mua cua shop la xanh/cyan; loc theo mau de khong phu thuoc vao so tien tren nut.
    lower_blue = np.array([80, 55, 120], dtype=np.uint8)
    upper_blue = np.array([110, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower_blue, upper_blue)
    kernel = np.ones((5, 5), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[int, int, int, int, int]] = []
    for contour in contours:
        x_pos, y_pos, width, height = cv2.boundingRect(contour)
        area = width * height
        if area < 450 or width < 32 or height < 18:
            continue
        candidates.append((area, x_pos, y_pos, width, height))

    if not candidates:
        return None

    _, x_pos, y_pos, width, height = max(candidates, key=lambda item: item[0])
    return left + x_pos + width // 2, top + y_pos + height // 2


def find_red_button_point_in_action_region(
    context: BotContext,
    frame,
    action_name: str,
) -> Optional[tuple[int, int]]:
    left, top, right, bottom = resolve_action_search_region(context, action_name, frame)
    roi = frame[top:bottom, left:right]
    if roi.size == 0:
        return None

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    lower_red_1 = np.array([0, 70, 110], dtype=np.uint8)
    upper_red_1 = np.array([12, 255, 255], dtype=np.uint8)
    lower_red_2 = np.array([168, 70, 110], dtype=np.uint8)
    upper_red_2 = np.array([180, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower_red_1, upper_red_1) | cv2.inRange(
        hsv,
        lower_red_2,
        upper_red_2,
    )
    kernel = np.ones((5, 5), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[int, int, int, int, int]] = []
    for contour in contours:
        x_pos, y_pos, width, height = cv2.boundingRect(contour)
        area = width * height
        if area < 300 or width < 20 or height < 16:
            continue
        candidates.append((area, x_pos, y_pos, width, height))

    if not candidates:
        return None

    button_config = get_navigation_config(context, action_name)
    expected_point = resolve_click_position(
        button_config,
        (context.window.width, context.window.height),
        context.reference_client_size,
    )
    if expected_point is not None:
        _, x_pos, y_pos, width, height = min(
            candidates,
            key=lambda item: (
                (left + item[1] + item[3] // 2 - expected_point[0]) ** 2
                + (top + item[2] + item[4] // 2 - expected_point[1]) ** 2,
                -item[0],
            ),
        )
    else:
        _, x_pos, y_pos, width, height = max(candidates, key=lambda item: item[0])
    return left + x_pos + width // 2, top + y_pos + height // 2


def click_seed_blue_button(context: BotContext, action_name: str, frame, label: str) -> bool:
    if click_named_button(context, action_name, frame):
        return True

    click_point = find_blue_button_point_in_action_region(context, frame, action_name)
    if click_point is None or context.window is None:
        return False

    context.mouse.click_relative(context.window, click_point)
    log(f"Clicked '{action_name}' bang nhan dien mau xanh tai {click_point} ({label}).")
    sleep_random(context.config["timing"]["click_delay_seconds"])
    return True


def click_detected_action_only(context: BotContext, action_name: str, frame, log_label: str | None = None) -> bool:
    if context.window is None:
        return False

    button_match = context.detector.find_action_button(frame, action_name)
    if button_match is None:
        return False

    button_config = get_navigation_config(context, action_name)
    click_point = button_match.click_point
    offset_x, offset_y = resolve_click_offset(
        button_config,
        (context.window.width, context.window.height),
        context.reference_client_size,
    )
    if offset_x or offset_y:
        click_point = clamp_point(
            (click_point[0] + offset_x, click_point[1] + offset_y),
            (context.window.width, context.window.height),
        )

    context.mouse.click_relative(context.window, click_point)
    label = log_label or action_name
    log(f"Clicked '{label}' chi khi template hien thi tai {click_point}.")
    sleep_random(context.config["timing"]["click_delay_seconds"])
    return True


def click_sell_entry_button(context: BotContext, frame) -> bool:
    if click_named_button(context, "sell_entry", frame):
        return True

    click_point = find_red_button_point_in_action_region(context, frame, "sell_entry")
    if click_point is not None and context.window is not None:
        context.mouse.click_relative(context.window, click_point)
        log(f"Clicked 'sell_entry' bang nhan dien mau do tai {click_point}.")
        sleep_random(context.config["timing"]["click_delay_seconds"])
        return True

    return False


def reset_session(context: BotContext) -> None:
    context.active_row = None
    context.scroll_attempts = 0
    context.no_match_search_attempts = 0
    context.state_attempts = 0
    context.session_harvest_count = 0
    context.harvest_navigation_recoveries = 0
    context.restart_harvest_immediately = False
    context.sell_flow_active = False
    context.seed_flow_active = False
    context.seed_target_index = 0


def schedule_next_check(context: BotContext, reason: str) -> None:
    wait_seconds = random_in_range(context.config["scheduler"]["idle_check_interval_seconds"])
    context.next_check_time = time.monotonic() + wait_seconds
    log(f"{reason}. Lan kiem tra tiep theo sau {wait_seconds:.1f} giay.")


def begin_harvest_session(context: BotContext) -> None:
    reset_session(context)
    set_state(context, BotState.OPEN_HOME)


def begin_seed_purchase_flow(context: BotContext) -> None:
    context.sell_flow_active = False
    context.seed_flow_active = True
    context.seed_target_index = 0
    context.state_attempts = 0
    log("Bat dau quy trinh mua danh sach hat giong truoc khi ban nong san.")
    set_state(context, BotState.OPEN_SEED_SHOP_ENTRY)


def begin_sell_flow(context: BotContext) -> None:
    context.seed_flow_active = False
    context.sell_flow_active = True
    context.state_attempts = 0
    log("Bat dau quy trinh ban nong san.")
    set_state(context, BotState.OPEN_SELL_ENTRY)


def get_seed_targets(context: BotContext) -> list[dict]:
    return list(context.config.get("workflow", {}).get("seed_purchase_targets", []))


def get_current_seed_target(context: BotContext) -> Optional[dict]:
    targets = get_seed_targets(context)
    if context.seed_target_index < 0 or context.seed_target_index >= len(targets):
        return None
    return targets[context.seed_target_index]


def get_current_seed_display_name(context: BotContext) -> str:
    target = get_current_seed_target(context)
    if target is None:
        return "hat giong"
    return str(target.get("display_name") or target.get("name") or "hat giong")


def advance_to_next_seed_target(context: BotContext) -> None:
    context.seed_target_index += 1
    context.state_attempts = 0
    target = get_current_seed_target(context)
    if target is None:
        log("Da xu ly het danh sach hat giong. Dong shop va tiep tuc ban nong san.")
        set_state(context, BotState.CLOSE_SEED_SHOP)
        return

    log(f"Chuyen sang tim mua {get_current_seed_display_name(context)}.")
    set_state(context, BotState.SEARCH_PUMPKIN_SEED)


def find_visible_seed_target_index(context: BotContext, frame, start_index: int) -> Optional[int]:
    """Tim target hat giong ke tiep dang hien tren viewport hien tai."""
    targets = get_seed_targets(context)
    for index in range(max(0, start_index), len(targets)):
        action_name = str(targets[index].get("row_action", ""))
        if action_name and context.detector.find_action_button(frame, action_name) is not None:
            return index
    return None


def finish_harvest_session(context: BotContext, reason: str) -> None:
    schedule_next_check(context, reason)
    reset_session(context)
    set_state(context, BotState.WAIT_FOR_CHECK)


def handle_wait_for_check(context: BotContext) -> None:
    if time.monotonic() >= context.next_check_time:
        begin_harvest_session(context)
        return

    sleep_random(context.config["timing"]["loop_delay_seconds"])


def handle_navigation_step(
    context: BotContext,
    action_name: str,
    success_state: BotState,
    failure_reason: str,
    already_success_predicate=None,
    session_done_if_exhausted: bool = True,
    retry_forever: bool = False,
) -> None:
    frame = capture_frame(context)
    if sync_state_from_visible_screen(context, frame):
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    if already_success_predicate is not None and already_success_predicate(context, frame):
        log(f"Bo qua click '{action_name}' vi da o dung man hinh cho state tiep theo.")
        set_state(context, success_state)
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return
    if click_named_button(context, action_name, frame):
        set_state(context, success_state)
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    context.state_attempts += 1
    max_retries = int(context.config["navigation"]["max_button_search_retries"])
    if context.state_attempts >= max_retries:
        if retry_forever:
            log(f"Chua tim thay '{action_name}'. Tiep tuc scan thay vi ket thuc bot.")
            context.state_attempts = 0
            sleep_random(context.config["timing"]["button_retry_delay_seconds"])
            return
        if session_done_if_exhausted:
            finish_harvest_session(context, failure_reason)
        else:
            log(f"Het so lan thu cho state '{context.state.name}'.")
            set_state(context, BotState.SESSION_DONE)
    else:
        sleep_random(context.config["timing"]["button_retry_delay_seconds"])


def handle_open_available_to_harvest(context: BotContext) -> None:
    frame = capture_frame(context)
    if sync_state_from_visible_screen(context, frame):
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    if is_harvest_popup_visible(context, frame):
        log("Da o san popup quan ly nha. Bo qua click 'Co the thu hoach'.")
        context.harvest_navigation_recoveries = 0
        set_state(context, BotState.OPEN_HARVEST_POPUP)
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    if click_named_button(context, "available_to_harvest", frame):
        context.harvest_navigation_recoveries = 0
        set_state(context, BotState.OPEN_HARVEST_POPUP)
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    context.state_attempts += 1
    max_retries = int(context.config["navigation"]["max_button_search_retries"])
    if context.state_attempts < max_retries:
        sleep_random(context.config["timing"]["button_retry_delay_seconds"])
        return

    max_recoveries = int(
        context.config.get("workflow", {}).get("harvest_navigation_recovery_attempts", 2)
    )
    if context.harvest_navigation_recoveries < max_recoveries:
        context.harvest_navigation_recoveries += 1
        log(
            "Chua thay 'Co the thu hoach'. Quay lai scan man hinh chinh "
            f"({context.harvest_navigation_recoveries}/{max_recoveries})."
        )
        set_state(context, BotState.OPEN_HOME)
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    finish_harvest_session(
        context,
        "Khong thay 'Co the thu hoach'. Co the chua den gio thu hoach",
    )


def is_available_to_harvest_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "available_to_harvest") is not None


def is_harvest_popup_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "harvest_fruit_popup") is not None


def is_fruit_list_visible(context: BotContext, frame) -> bool:
    if context.detector.find_action_button(frame, "fruit_harvest") is not None:
        return True
    return bool(context.detector.find_harvestable_rows(frame))


def is_sell_entry_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "sell_entry") is not None


def is_sell_cart_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "sell_cart") is not None


def is_npc_dialog_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "npc_dialog_continue") is not None


def is_sell_produce_option_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "sell_produce_option") is not None


def is_sell_auto_select_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "sell_auto_select") is not None


def is_sell_bottom_submit_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "sell_bottom_submit") is not None


def is_sell_popup_submit_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "sell_popup_submit") is not None


def is_sell_final_confirm_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "sell_final_confirm") is not None


def is_sell_success_ok_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "sell_success_ok") is not None


def is_sell_screen_close_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "sell_screen_close") is not None


def is_seed_shop_entry_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "seed_shop_entry") is not None


def is_seed_shop_buy_option_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "seed_shop_buy_option") is not None


def is_seed_shop_npc_trigger_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "seed_shop_npc_trigger") is not None


def is_seed_shop_leave_option_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "seed_shop_leave_option") is not None


def is_seed_shop_menu_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "seed_shop_close") is not None


def is_seed_shop_final_dialog_continue_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "seed_shop_final_dialog_continue") is not None


def is_pumpkin_seed_row_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "seed_pumpkin_row") is not None


def is_pumpkin_seed_detail_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "seed_pumpkin_detail") is not None


def is_pumpkin_seed_sold_out_visible(context: BotContext, frame) -> bool:
    return context.detector.find_action_button(frame, "seed_pumpkin_sold_out") is not None


def sync_state_from_visible_screen(context: BotContext, frame) -> bool:
    seed_states = {
        BotState.OPEN_SEED_SHOP_ENTRY,
        BotState.OPEN_SEED_SHOP_NPC_MENU,
        BotState.OPEN_SEED_SHOP_BUY_OPTION,
        BotState.SEARCH_PUMPKIN_SEED,
        BotState.SELECT_PUMPKIN_SEED,
        BotState.BUY_PUMPKIN_SEED,
        BotState.CONFIRM_BUY_PUMPKIN_SEED,
        BotState.CLOSE_SEED_SHOP,
        BotState.LEAVE_SEED_SHOP_MENU,
        BotState.DISMISS_SEED_SHOP_END_DIALOG,
    }
    sell_states = {
        BotState.OPEN_SELL_ENTRY,
        BotState.OPEN_SELL_CART,
        BotState.ADVANCE_SELL_NPC_DIALOG,
        BotState.OPEN_SELL_PRODUCE_OPTION,
        BotState.SELL_AUTO_SELECT,
        BotState.SELL_SUBMIT_SELECTION,
        BotState.SELL_CONFIRM_POPUP_SUBMIT,
        BotState.SELL_FINAL_CONFIRM,
        BotState.SELL_SUCCESS_OK,
        BotState.SELL_CLOSE_SCREEN,
        BotState.SELL_DISMISS_END_DIALOG,
    }

    should_sync_seed = context.seed_flow_active or context.state in seed_states
    should_sync_sell = context.sell_flow_active or context.state in sell_states

    if (
        should_sync_seed
        and context.state in {
            BotState.CLOSE_SEED_SHOP,
            BotState.LEAVE_SEED_SHOP_MENU,
            BotState.DISMISS_SEED_SHOP_END_DIALOG,
        }
        and is_seed_shop_leave_option_visible(context, frame)
    ):
        if context.state != BotState.LEAVE_SEED_SHOP_MENU:
            log("Da o san menu NPC cua cua hang hat giong.")
            set_state(context, BotState.LEAVE_SEED_SHOP_MENU)
            return True
        return False

    if (
        should_sync_seed
        and context.state in {
            BotState.LEAVE_SEED_SHOP_MENU,
            BotState.DISMISS_SEED_SHOP_END_DIALOG,
        }
        and not is_seed_shop_leave_option_visible(context, frame)
        and is_seed_shop_final_dialog_continue_visible(context, frame)
    ):
        if context.state != BotState.DISMISS_SEED_SHOP_END_DIALOG:
            log("Da o san hoi thoai cam on sau khi roi shop hat giong.")
            set_state(context, BotState.DISMISS_SEED_SHOP_END_DIALOG)
            return True
        return False

    if (
        should_sync_seed
        and context.state
        not in {
            BotState.CLOSE_SEED_SHOP,
            BotState.LEAVE_SEED_SHOP_MENU,
            BotState.DISMISS_SEED_SHOP_END_DIALOG,
        }
        and (get_current_seed_target(context) or {}).get("name") == "pumpkin"
        and is_pumpkin_seed_detail_visible(context, frame)
    ):
        if context.state not in {BotState.BUY_PUMPKIN_SEED, BotState.CONFIRM_BUY_PUMPKIN_SEED}:
            log("Da chon san 'Hat bi ngo' trong cua hang hat giong.")
            set_state(context, BotState.BUY_PUMPKIN_SEED)
            return True
        return False

    if (
        should_sync_seed
        and context.state
        not in {
            BotState.CLOSE_SEED_SHOP,
            BotState.LEAVE_SEED_SHOP_MENU,
            BotState.DISMISS_SEED_SHOP_END_DIALOG,
        }
        and is_seed_shop_menu_visible(context, frame)
    ):
        if context.state not in {
            BotState.SEARCH_PUMPKIN_SEED,
            BotState.SELECT_PUMPKIN_SEED,
            BotState.BUY_PUMPKIN_SEED,
            BotState.CONFIRM_BUY_PUMPKIN_SEED,
            BotState.CLOSE_SEED_SHOP,
        }:
            log("Da o san giao dien mua hat giong.")
            set_state(context, BotState.SEARCH_PUMPKIN_SEED)
            return True
        return False

    if should_sync_seed and is_seed_shop_buy_option_visible(context, frame):
        if context.state != BotState.OPEN_SEED_SHOP_BUY_OPTION:
            log("Da o san menu NPC voi lua chon 'Mua'.")
            set_state(context, BotState.OPEN_SEED_SHOP_BUY_OPTION)
            return True
        return False

    if should_sync_sell and is_sell_success_ok_visible(context, frame):
        if context.state != BotState.SELL_SUCCESS_OK:
            log("Da o san popup thong bao ban hang thanh cong.")
            set_state(context, BotState.SELL_SUCCESS_OK)
            return True
        return False

    if should_sync_sell and is_sell_final_confirm_visible(context, frame):
        if context.state != BotState.SELL_FINAL_CONFIRM:
            log("Da o san popup xac nhan cuoi cung cua buoc ban.")
            set_state(context, BotState.SELL_FINAL_CONFIRM)
            return True
        return False

    if should_sync_sell and is_sell_popup_submit_visible(context, frame):
        if context.state != BotState.SELL_CONFIRM_POPUP_SUBMIT:
            log("Da o san popup xac nhan ban hang voi nut xanh so tien.")
            set_state(context, BotState.SELL_CONFIRM_POPUP_SUBMIT)
            return True
        return False

    if should_sync_sell and context.state == BotState.SELL_CLOSE_SCREEN and is_sell_screen_close_visible(context, frame):
        return False

    if should_sync_sell and is_sell_bottom_submit_visible(context, frame):
        if context.state not in {BotState.SELL_SUBMIT_SELECTION, BotState.SELL_AUTO_SELECT}:
            log("Da co nut xanh xac nhan ban o giao dien 'Ban nong san'.")
            set_state(context, BotState.SELL_SUBMIT_SELECTION)
            return True
        if context.state == BotState.SELL_SUBMIT_SELECTION:
            return False

    if should_sync_sell and is_sell_auto_select_visible(context, frame):
        if context.state not in {BotState.SELL_AUTO_SELECT, BotState.SELL_SUBMIT_SELECTION}:
            log("Da o san giao dien 'Ban nong san'.")
            set_state(context, BotState.SELL_AUTO_SELECT)
            return True
        if context.state == BotState.SELL_AUTO_SELECT:
            return False

    if should_sync_sell and is_sell_produce_option_visible(context, frame):
        if context.state != BotState.OPEN_SELL_PRODUCE_OPTION:
            log("Da o san popup chon 'Ban nong san'.")
            set_state(context, BotState.OPEN_SELL_PRODUCE_OPTION)
            return True
        return False

    if should_sync_sell and is_npc_dialog_visible(context, frame):
        target_state = (
            BotState.SELL_DISMISS_END_DIALOG
            if context.state in {BotState.SELL_CLOSE_SCREEN, BotState.SELL_DISMISS_END_DIALOG}
            else BotState.ADVANCE_SELL_NPC_DIALOG
        )
        if context.state != target_state:
            log("Da o san hoi thoai NPC trong quy trinh ban.")
            set_state(context, target_state)
            return True
        return False

    if should_sync_sell:

        if is_sell_cart_visible(context, frame):
            if context.state == BotState.SELL_DISMISS_END_DIALOG:
                set_state(context, BotState.SESSION_DONE)
                return True
            if context.state != BotState.OPEN_SELL_CART:
                log("Da o san cua hang ban voi nut gio hang trang.")
                set_state(context, BotState.OPEN_SELL_CART)
                return True
            return False

        if is_sell_entry_visible(context, frame):
            if context.state != BotState.OPEN_SELL_ENTRY:
                log("Da o san man hinh chinh co nut 'Ban'.")
                set_state(context, BotState.OPEN_SELL_ENTRY)
                return True
            return False

    if is_fruit_list_visible(context, frame):
        if context.state in {
            BotState.OPEN_HOME,
            BotState.OPEN_AVAILABLE_TO_HARVEST,
            BotState.OPEN_HARVEST_POPUP,
        }:
            log("Da o san trong danh sach trai cay. Chuyen thang sang buoc tim dong can thu hoach.")
            set_state(context, BotState.SEARCH_TARGET_ROWS)
            return True

    if is_harvest_popup_visible(context, frame):
        if context.state in {BotState.OPEN_HOME, BotState.OPEN_AVAILABLE_TO_HARVEST}:
            log("Da o san popup quan ly nha. Chuyen thang sang buoc click 'Thu hoach trai'.")
            set_state(context, BotState.OPEN_HARVEST_POPUP)
            return True

    if is_available_to_harvest_visible(context, frame):
        if context.state == BotState.OPEN_HOME:
            log("Da o san man hinh co 'Co the thu hoach'. Bo qua buoc 'Nha ta'.")
            set_state(context, BotState.OPEN_AVAILABLE_TO_HARVEST)
            return True

    return False


def bootstrap_visible_flow_on_start(context: BotContext) -> bool:
    if context.startup_flow_bootstrapped:
        return False

    context.startup_flow_bootstrapped = True
    frame = capture_frame(context)
    if dismiss_harvest_interrupt_popup(context, frame):
        log("Da dong popup thong bao thu hoach luc khoi dong. Tiep tuc danh sach hien tai.")
        return True

    if any(
        (
            is_sell_success_ok_visible(context, frame),
            is_sell_final_confirm_visible(context, frame),
            is_sell_popup_submit_visible(context, frame),
            is_sell_auto_select_visible(context, frame),
            is_sell_bottom_submit_visible(context, frame),
            is_sell_produce_option_visible(context, frame),
            is_npc_dialog_visible(context, frame),
            is_sell_cart_visible(context, frame),
        )
    ):
        log("Phat hien dang o giua quy trinh ban. Tiep tuc tu man hinh hien tai.")
        context.sell_flow_active = True
        return sync_state_from_visible_screen(context, frame)
    if any(
        (
            is_seed_shop_buy_option_visible(context, frame),
            is_seed_shop_leave_option_visible(context, frame),
            is_seed_shop_menu_visible(context, frame),
            is_seed_shop_npc_trigger_visible(context, frame),
        )
    ):
        log("Phat hien dang o giua quy trinh mua hat giong. Tiep tuc tu man hinh hien tai.")
        context.seed_flow_active = True
        if sync_state_from_visible_screen(context, frame):
            return True
        if is_seed_shop_npc_trigger_visible(context, frame):
            set_state(context, BotState.OPEN_SEED_SHOP_NPC_MENU)
            return True
        return False
    return False


def dismiss_harvest_interrupt_popup(context: BotContext, frame) -> bool:
    if context.detector.find_message(frame, "harvest_interrupt") is None:
        return False

    if context.detector.find_action_button(frame, "harvest_interrupt_ok") is None:
        return False

    log("Phat hien popup thong bao trong luc thu hoach. Click 'OK' de tiep tuc.")
    if click_named_button(context, "harvest_interrupt_ok", frame):
        context.active_row = None
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        set_state(context, BotState.SEARCH_TARGET_ROWS)
    return True


def handle_search_target_rows(context: BotContext) -> None:
    frame = capture_frame(context)

    if dismiss_harvest_interrupt_popup(context, frame):
        return

    if context.detector.find_message(frame, "bag_full") is not None:
        set_state(context, BotState.BAG_FULL)
        return

    rows = context.detector.find_harvestable_rows(frame)
    if rows:
        context.no_match_search_attempts = 0
        context.active_row = rows[0]
        set_state(context, BotState.HARVEST_ROW)
        return

    if context.detector.find_message(frame, "end_of_harvest_list") is not None:
        log("Da thay 'Dau tay' o cuoi danh sach. Dong popup thu hoach bang nut X.")
        if click_named_button(context, "close_harvest_popup", frame):
            if (
                context.session_harvest_count > 0
                and context.config.get("workflow", {}).get("buy_seed_before_sell", True)
            ):
                log(
                    "Da hoan tat danh sach thu hoach. "
                    "Chuyen sang mua hat giong truoc khi ban."
                )
                sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
                begin_seed_purchase_flow(context)
            else:
                finish_harvest_session(
                    context,
                    f"Da cuon het danh sach. Da harvest {context.session_harvest_count} lan",
                )
        else:
            log("Khong dong duoc popup thu hoach sau khi thay 'Dau tay'.")
        return

    retry_before_scroll = int(
        context.config["navigation"]["scroll"].get("search_retries_before_scroll", 2)
    )
    if context.no_match_search_attempts < retry_before_scroll:
        context.no_match_search_attempts += 1
        sleep_random(context.config["timing"].get("row_search_retry_delay_seconds", [0.5, 0.9]))
        return

    context.no_match_search_attempts = 0
    max_scroll_attempts = int(context.config["navigation"]["scroll"]["max_attempts"])
    if max_scroll_attempts > 0 and context.scroll_attempts >= max_scroll_attempts:
        finish_harvest_session(
            context,
            f"Khong tim thay them dong trai can thu hoach. Da harvest {context.session_harvest_count} lan",
        )
        return

    set_state(context, BotState.SCROLL_LIST)


def handle_harvest_row(context: BotContext) -> None:
    if context.window is None or context.active_row is None:
        set_state(context, BotState.SEARCH_TARGET_ROWS)
        return

    frame = capture_frame(context)
    if dismiss_harvest_interrupt_popup(context, frame):
        return

    row = context.active_row
    button_config = get_navigation_config(context, "fruit_harvest")
    offset_x, offset_y = resolve_click_offset(
        button_config,
        (context.window.width, context.window.height),
        context.reference_client_size,
    )
    click_point = row.button_point
    if offset_x or offset_y:
        click_point = clamp_point(
            (row.button_point[0] + offset_x, row.button_point[1] + offset_y),
            (context.window.width, context.window.height),
        )

    context.mouse.click_relative(context.window, click_point)
    context.session_harvest_count += 1
    log(
        f"Harvest '{row.fruit_name}' tai button {click_point} "
        f"(label_score={row.label_score:.3f}, button_score={row.button_score:.3f})"
    )

    sleep_random(context.config["timing"]["post_harvest_click_wait_seconds"])
    context.active_row = None
    set_state(context, BotState.SEARCH_TARGET_ROWS)


def handle_scroll_list(context: BotContext) -> None:
    if context.window is None:
        return

    scroll_config = context.config["navigation"]["scroll"]
    hover_point = get_scroll_hover_point(context)
    wheel_delta = int(scroll_config.get("down_wheel_delta", -360))
    context.mouse.scroll_relative(context.window, hover_point, wheel_delta)
    context.scroll_attempts += 1
    context.no_match_search_attempts = 0
    log(f"Da cuon danh sach xuong. So lan cuon: {context.scroll_attempts}")
    sleep_random(context.config["timing"]["scroll_delay_seconds"])
    set_state(context, BotState.SEARCH_TARGET_ROWS)


def handle_bag_full(context: BotContext) -> None:
    frame = capture_frame(context)
    log("Phat hien thong bao day tui. Thu dong popup bang nut X.")

    if click_named_button(context, "close_harvest_popup", frame):
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
    else:
        log("Khong dong duoc popup day tui bang nut X.")

    workflow_config = context.config.get("workflow", {})
    if workflow_config.get("sell_when_bag_full", True):
        if workflow_config.get("buy_seed_before_sell", True):
            begin_seed_purchase_flow(context)
        else:
            log("Bat dau quy trinh ban nong san sau khi day tui.")
            begin_sell_flow(context)
        return

    if workflow_config.get("stop_when_bag_full", True):
        set_state(context, BotState.SESSION_DONE)
        return

    finish_harvest_session(context, "Day tui")


def handle_search_pumpkin_seed(context: BotContext) -> None:
    frame = capture_frame(context)
    if sync_state_from_visible_screen(context, frame):
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    target = get_current_seed_target(context)
    if target is None:
        set_state(context, BotState.CLOSE_SEED_SHOP)
        return

    target_name = get_current_seed_display_name(context)
    target_action = str(target.get("row_action", ""))
    if not target_action:
        log(f"Target {target_name} chua cau hinh row_action. Bo qua.")
        advance_to_next_seed_target(context)
        return

    if click_named_button(context, target_action, frame):
        set_state(context, BotState.SELECT_PUMPKIN_SEED)
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    context.state_attempts += 1
    max_scrolls = int(
        context.config.get("workflow", {}).get(
            "seed_shop_max_scroll_attempts_per_target",
            context.config.get("workflow", {}).get("seed_shop_max_scroll_attempts", 8),
        )
    )
    if context.state_attempts > max_scrolls:
        log(f"Khong tim thay {target_name} trong shop. Bo qua target nay.")
        advance_to_next_seed_target(context)
        return

    if context.window is None:
        return

    seed_scroll_ratio = context.config.get("workflow", {}).get(
        "seed_shop_scroll_point_ratio",
        [0.322, 0.577],
    )
    hover_point = (
        round(context.window.width * float(seed_scroll_ratio[0])),
        round(context.window.height * float(seed_scroll_ratio[1])),
    )
    workflow_config = context.config.get("workflow", {})
    if context.seed_target_index == 0:
        wheel_delta = int(workflow_config.get("seed_shop_initial_wheel_delta", -420))
        distance_pixels = int(workflow_config.get("seed_shop_initial_scroll_distance_pixels", 420))
        scroll_label = "Scroll nhanh"
    else:
        wheel_delta = int(workflow_config.get("seed_shop_wheel_delta", -180))
        distance_pixels = int(workflow_config.get("seed_shop_scroll_distance_pixels", 150))
        scroll_label = "Scroll nhe"
    log(
        f"Chua thay {target_name}. {scroll_label} shop hat giong "
        f"lan {context.state_attempts}/{max_scrolls}."
    )
    context.mouse.scroll_relative(context.window, hover_point, wheel_delta, distance_pixels)
    sleep_random(context.config["timing"]["scroll_delay_seconds"])


def handle_select_pumpkin_seed(context: BotContext) -> None:
    set_state(context, BotState.BUY_PUMPKIN_SEED)


def handle_buy_pumpkin_seed(context: BotContext) -> None:
    frame = capture_frame(context)
    target_name = get_current_seed_display_name(context)
    if get_current_seed_target(context) is None:
        set_state(context, BotState.CLOSE_SEED_SHOP)
        return

    if not is_seed_shop_menu_visible(context, frame):
        log(f"Chua thay giao dien shop khi xu ly {target_name}. Quay lai tim trong danh sach hat giong.")
        set_state(context, BotState.SEARCH_PUMPKIN_SEED)
        return

    if click_seed_blue_button(context, "seed_buy_price", frame, target_name):
        set_state(context, BotState.CONFIRM_BUY_PUMPKIN_SEED)
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    if is_pumpkin_seed_sold_out_visible(context, frame):
        log(f"{target_name} dang het hang. Bo qua va xu ly hat tiep theo.")
        advance_to_next_seed_target(context)
        return

    log(f"Khong thay nut gia tien de mua {target_name}. Bo qua target nay.")
    advance_to_next_seed_target(context)


def handle_confirm_buy_pumpkin_seed(context: BotContext) -> None:
    frame = capture_frame(context)
    target_name = get_current_seed_display_name(context)
    if click_seed_blue_button(context, "seed_buy_popup_submit", frame, target_name):
        log(f"Da click xac nhan mua {target_name}.")
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        advance_to_next_seed_target(context)
        return

    context.state_attempts += 1
    max_retries = int(context.config["navigation"]["max_button_search_retries"])
    if context.state_attempts >= max_retries:
        log(f"Khong xac nhan duoc popup mua {target_name}. Bo qua target nay.")
        advance_to_next_seed_target(context)
    else:
        sleep_random(context.config["timing"]["button_retry_delay_seconds"])


def handle_dismiss_seed_shop_end_dialog(context: BotContext) -> None:
    required_dialog_clicks = int(
        context.config.get("workflow", {}).get("seed_shop_final_dialog_clicks", 2)
    )
    max_attempts_per_click = 3
    successful_clicks = 0
    dialog_wait = context.config["timing"].get(
        "seed_shop_final_dialog_wait_seconds",
        [1.35, 1.75],
    )

    for dialog_index in range(required_dialog_clicks):
        sleep_random(dialog_wait)

        clicked_this_dialog = False
        for _ in range(max_attempts_per_click):
            frame = capture_frame(context)
            if not is_seed_shop_final_dialog_continue_visible(context, frame):
                if successful_clicks == 0:
                    log("Khong con thay hoi thoai cam on cua shop hat giong. Chuyen sang luong ban.")
                else:
                    log("Hoi thoai cam on cua shop hat giong da bien mat. Chuyen sang luong ban.")
                break

            if click_detected_action_only(
                context,
                "seed_shop_final_dialog_continue",
                frame,
                "seed_shop_final_dialog_continue",
            ):
                successful_clicks += 1
                clicked_this_dialog = True
                log(
                    f"Da click ket thuc hoi thoai shop lan "
                    f"{successful_clicks}/{required_dialog_clicks}."
                )
                sleep_random(dialog_wait)
                break

            sleep_random(context.config["timing"]["button_retry_delay_seconds"])

        if not clicked_this_dialog:
            break

    final_frame = capture_frame(context)
    if (
        successful_clicks < required_dialog_clicks
        and is_seed_shop_final_dialog_continue_visible(context, final_frame)
    ):
        log(
            f"Chua xac nhan du {required_dialog_clicks} nhip hoi thoai ket thuc shop hat giong. "
            "Van chuyen tiep sang luong ban va de recovery xu ly phan con sot."
        )

    begin_sell_flow(context)


def handle_leave_seed_shop_menu(context: BotContext) -> None:
    click_attempts = int(
        context.config.get("workflow", {}).get("seed_shop_leave_option_click_attempts", 3)
    )
    for attempt in range(click_attempts):
        frame = capture_frame(context)
        if (
            is_seed_shop_final_dialog_continue_visible(context, frame)
            and not is_seed_shop_leave_option_visible(context, frame)
        ):
            log("Da thay hoi thoai cam on sau khi bam 'Roi khoi'.")
            set_state(context, BotState.DISMISS_SEED_SHOP_END_DIALOG)
            sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
            return

        if not is_seed_shop_leave_option_visible(context, frame):
            if sync_state_from_visible_screen(context, frame):
                sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
                return

        if click_named_button(context, "seed_shop_leave_option", frame):
            sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
            validation_frame = capture_frame(context)
            leave_option_still_visible = is_seed_shop_leave_option_visible(
                context,
                validation_frame,
            )
            if (
                is_seed_shop_final_dialog_continue_visible(context, validation_frame)
                and not leave_option_still_visible
            ):
                log("Da vao hoi thoai cam on sau khi bam 'Roi khoi'.")
                set_state(context, BotState.DISMISS_SEED_SHOP_END_DIALOG)
                sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
                return

            if leave_option_still_visible:
                log("Da bam 'Roi khoi' nhung menu van con. Thu lai trong state hien tai.")
                if attempt < click_attempts - 1:
                    sleep_random(context.config["timing"]["button_retry_delay_seconds"])
                    continue
                break

            sleep_random(
                context.config["timing"].get(
                    "seed_shop_final_dialog_wait_seconds",
                    [1.35, 1.75],
                )
            )
            validation_frame = capture_frame(context)
            if (
                is_seed_shop_final_dialog_continue_visible(context, validation_frame)
                and not is_seed_shop_leave_option_visible(context, validation_frame)
            ):
                log("Da vao hoi thoai cam on sau khi bam 'Roi khoi'.")
                set_state(context, BotState.DISMISS_SEED_SHOP_END_DIALOG)
                return

            log("Da thoat menu NPC cua shop hat giong. Chuyen sang luong ban.")
            begin_sell_flow(context)
            return

        if attempt < click_attempts - 1:
            log(
                f"Chua thay 'Roi khoi'. Thu lai trong cung state "
                f"({attempt + 1}/{click_attempts})."
            )
            sleep_random(context.config["timing"]["button_retry_delay_seconds"])

    context.state_attempts += 1
    max_retries = int(context.config["navigation"]["max_button_search_retries"])
    if context.state_attempts >= max_retries:
        log("Khong chon duoc 'Roi khoi' cua hang hat giong sau nhieu lan thu.")
        set_state(context, BotState.SESSION_DONE)
    else:
        sleep_random(context.config["timing"]["button_retry_delay_seconds"])


def recover_residual_seed_ui_before_sell(context: BotContext, frame) -> bool:
    if is_seed_shop_menu_visible(context, frame):
        log("Con sot giao dien shop hat giong. Dong bang nut X truoc khi tim 'Ban'.")
        if click_named_button(context, "seed_shop_close", frame):
            return True

    if is_seed_shop_leave_option_visible(context, frame):
        log("Con sot menu NPC cua shop hat giong. Chon 'Roi khoi' truoc khi tim 'Ban'.")
        if click_named_button(context, "seed_shop_leave_option", frame):
            return True

    if is_seed_shop_final_dialog_continue_visible(context, frame):
        log("Con sot hoi thoai cam on sau khi roi shop hat giong. Click tiep de dong truoc khi tim 'Ban'.")
        if click_named_button(context, "seed_shop_final_dialog_continue", frame):
            return True

    if is_seed_shop_buy_option_visible(context, frame):
        log("Popup NPC cua shop hat giong van mo. Thu dong bang nut 'Roi khoi' truoc khi tim 'Ban'.")
        if click_named_button(context, "seed_shop_leave_option", frame):
            return True

    return False


def handle_open_sell_entry(context: BotContext) -> None:
    frame = capture_frame(context)
    if sync_state_from_visible_screen(context, frame):
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    if recover_residual_seed_ui_before_sell(context, frame):
        context.state_attempts = 0
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    if click_sell_entry_button(context, frame):
        set_state(context, BotState.OPEN_SELL_CART)
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    if is_fruit_list_visible(context, frame):
        log("Danh sach thu hoach van dang mo. Dong bang nut X truoc khi tim button 'Ban'.")
        if click_named_button(context, "close_harvest_popup", frame):
            context.state_attempts = 0
            sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
            return

    context.state_attempts += 1
    max_retries = int(context.config["navigation"]["max_button_search_retries"])
    if context.state_attempts >= max_retries:
        log("Chua tim thay 'sell_entry'. Tiep tuc scan thay vi ket thuc bot.")
        context.state_attempts = 0
    sleep_random(context.config["timing"]["button_retry_delay_seconds"])


def handle_sell_auto_select(context: BotContext) -> None:
    frame = capture_frame(context)
    if sync_state_from_visible_screen(context, frame):
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    if is_sell_bottom_submit_visible(context, frame):
        log("Danh sach da co san gia ban. Bo qua 'Chon tu dong' va chuyen sang nut xanh so tien.")
        click_sell_bottom_submit_and_route(context, frame)
        return

    if click_named_button(context, "sell_auto_select", frame):
        sleep_random(
            context.config["timing"].get(
                "sell_immediate_submit_delay_seconds",
                [0.25, 0.45],
            )
        )
        validation_frame = capture_frame(context)

        log("Da bam 'Chon tu dong'. Bam ngay nut xanh so tien ben canh de ban.")
        if click_sell_bottom_submit_and_route(context, validation_frame):
            return

        sleep_random(context.config["timing"]["button_retry_delay_seconds"])
        validation_frame = capture_frame(context)
        log("Thu lai nut xanh so tien sau mot nhip doi them.")
        if click_sell_bottom_submit_and_route(context, validation_frame):
            return

        if is_sell_screen_close_visible(context, validation_frame) and is_sell_auto_select_visible(context, validation_frame):
            log("Sau buoc 'Chon tu dong' khong xuat hien nut xanh so tien. Xem nhu khong con nong san de ban.")
            set_state(context, BotState.SELL_CLOSE_SCREEN)
            sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
            return

        set_state(context, BotState.SELL_SUBMIT_SELECTION)
        return

    context.state_attempts += 1
    max_retries = int(context.config["navigation"]["max_button_search_retries"])
    if context.state_attempts >= max_retries:
        log("Het so lan thu cho buoc 'Chon tu dong' trong quy trinh ban.")
        set_state(context, BotState.SESSION_DONE)
    else:
        sleep_random(context.config["timing"]["button_retry_delay_seconds"])


def click_sell_bottom_submit_and_route(context: BotContext, frame=None) -> bool:
    if frame is None:
        frame = capture_frame(context)

    if not click_named_button(context, "sell_bottom_submit", frame):
        return False

    sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
    validation_frame = capture_frame(context)

    if sync_state_from_visible_screen(context, validation_frame):
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return True

    if is_sell_screen_close_visible(context, validation_frame) and not any(
        (
            is_sell_popup_submit_visible(context, validation_frame),
            is_sell_final_confirm_visible(context, validation_frame),
            is_sell_success_ok_visible(context, validation_frame),
        )
    ):
        log("Khong hien popup sau khi bam nut xanh so tien. Xem nhu da ban het nong san.")
        set_state(context, BotState.SELL_CLOSE_SCREEN)
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return True

    set_state(context, BotState.SELL_CONFIRM_POPUP_SUBMIT)
    return True


def handle_sell_submit_selection(context: BotContext) -> None:
    frame = capture_frame(context)
    if sync_state_from_visible_screen(context, frame):
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        return

    if click_sell_bottom_submit_and_route(context, frame):
        return

    context.state_attempts += 1
    max_retries = int(context.config["navigation"]["max_button_search_retries"])
    if context.state_attempts >= max_retries:
        log("Het so lan thu cho buoc gui danh sach nong san de ban.")
        set_state(context, BotState.SELL_CLOSE_SCREEN)
    else:
        sleep_random(context.config["timing"]["button_retry_delay_seconds"])


def handle_sell_end_dialog(context: BotContext) -> None:
    frame = capture_frame(context)
    if is_sell_cart_visible(context, frame):
        log("Da thoat khoi popup ban nong san.")
        context.restart_harvest_immediately = True
        set_state(context, BotState.SESSION_DONE)
        return

    if not any(
        (
            is_npc_dialog_visible(context, frame),
            is_sell_produce_option_visible(context, frame),
            is_sell_auto_select_visible(context, frame),
            is_sell_bottom_submit_visible(context, frame),
            is_sell_popup_submit_visible(context, frame),
            is_sell_final_confirm_visible(context, frame),
            is_sell_success_ok_visible(context, frame),
            is_sell_screen_close_visible(context, frame),
        )
    ):
        log("Da thoat khoi quy trinh ban va quay lai man hinh chinh.")
        context.restart_harvest_immediately = True
        set_state(context, BotState.SESSION_DONE)
        return

    if is_npc_dialog_visible(context, frame):
        if click_named_button(context, "npc_dialog_continue", frame):
            sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
            set_state(context, BotState.SELL_DISMISS_END_DIALOG)
            return

    context.state_attempts += 1
    max_retries = int(context.config["navigation"]["max_button_search_retries"])
    if context.state_attempts >= max_retries:
        log("Khong dong duoc hoi thoai ket thuc sau khi ban.")
        context.restart_harvest_immediately = True
        set_state(context, BotState.SESSION_DONE)
    else:
        sleep_random(context.config["timing"]["button_retry_delay_seconds"])


def run_state_machine(context: BotContext) -> None:
    log("Bot da chay. Nhan ESC de dung an toan.")
    if context.config["scheduler"].get("run_immediately_on_start", True):
        context.next_check_time = 0.0
    else:
        schedule_next_check(context, "Khoi tao lich")

    while True:
        if is_escape_pressed():
            log("Nhan ESC. Dang dung bot.")
            break

        if not refresh_game_window(context):
            sleep_random(context.config["timing"]["window_not_found_delay_seconds"])
            continue

        if bootstrap_visible_flow_on_start(context):
            sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
            continue

        if context.state == BotState.WAIT_FOR_CHECK:
            handle_wait_for_check(context)
        elif context.state == BotState.OPEN_HOME:
            handle_navigation_step(
                context,
                action_name="open_home",
                success_state=BotState.OPEN_AVAILABLE_TO_HARVEST,
                failure_reason="Khong mo duoc 'Nha ta'",
                already_success_predicate=is_available_to_harvest_visible,
                retry_forever=True,
            )
        elif context.state == BotState.OPEN_AVAILABLE_TO_HARVEST:
            handle_open_available_to_harvest(context)
        elif context.state == BotState.OPEN_HARVEST_POPUP:
            handle_navigation_step(
                context,
                action_name="harvest_fruit_popup",
                success_state=BotState.SEARCH_TARGET_ROWS,
                failure_reason="Khong mo duoc popup 'Thu hoach trai'",
                already_success_predicate=is_fruit_list_visible,
            )
        elif context.state == BotState.SEARCH_TARGET_ROWS:
            handle_search_target_rows(context)
        elif context.state == BotState.HARVEST_ROW:
            handle_harvest_row(context)
        elif context.state == BotState.SCROLL_LIST:
            handle_scroll_list(context)
        elif context.state == BotState.BAG_FULL:
            handle_bag_full(context)
        elif context.state == BotState.OPEN_SEED_SHOP_ENTRY:
            handle_navigation_step(
                context,
                action_name="seed_shop_entry",
                success_state=BotState.OPEN_SEED_SHOP_NPC_MENU,
                failure_reason="Khong mo duoc 'Cua hang hat giong'",
                already_success_predicate=lambda current_context, current_frame: (
                    is_seed_shop_npc_trigger_visible(current_context, current_frame)
                    or is_seed_shop_buy_option_visible(current_context, current_frame)
                    or is_seed_shop_menu_visible(current_context, current_frame)
                ),
                session_done_if_exhausted=False,
                retry_forever=True,
            )
        elif context.state == BotState.OPEN_SEED_SHOP_NPC_MENU:
            handle_navigation_step(
                context,
                action_name="seed_shop_npc_trigger",
                success_state=BotState.OPEN_SEED_SHOP_BUY_OPTION,
                failure_reason="Khong mo duoc menu NPC cua hang hat giong",
                already_success_predicate=is_seed_shop_buy_option_visible,
                session_done_if_exhausted=False,
            )
        elif context.state == BotState.OPEN_SEED_SHOP_BUY_OPTION:
            handle_navigation_step(
                context,
                action_name="seed_shop_buy_option",
                success_state=BotState.SEARCH_PUMPKIN_SEED,
                failure_reason="Khong mo duoc menu mua hat giong",
                already_success_predicate=is_seed_shop_menu_visible,
                session_done_if_exhausted=False,
                retry_forever=True,
            )
        elif context.state == BotState.SEARCH_PUMPKIN_SEED:
            handle_search_pumpkin_seed(context)
        elif context.state == BotState.SELECT_PUMPKIN_SEED:
            handle_select_pumpkin_seed(context)
        elif context.state == BotState.BUY_PUMPKIN_SEED:
            handle_buy_pumpkin_seed(context)
        elif context.state == BotState.CONFIRM_BUY_PUMPKIN_SEED:
            handle_confirm_buy_pumpkin_seed(context)
        elif context.state == BotState.CLOSE_SEED_SHOP:
            handle_navigation_step(
                context,
                action_name="seed_shop_close",
                success_state=BotState.LEAVE_SEED_SHOP_MENU,
                failure_reason="Khong dong duoc cua hang hat giong",
                already_success_predicate=is_seed_shop_leave_option_visible,
                session_done_if_exhausted=False,
            )
        elif context.state == BotState.LEAVE_SEED_SHOP_MENU:
            handle_leave_seed_shop_menu(context)
        elif context.state == BotState.DISMISS_SEED_SHOP_END_DIALOG:
            handle_dismiss_seed_shop_end_dialog(context)
        elif context.state == BotState.OPEN_SELL_ENTRY:
            handle_open_sell_entry(context)
        elif context.state == BotState.OPEN_SELL_CART:
            handle_navigation_step(
                context,
                action_name="sell_cart",
                success_state=BotState.ADVANCE_SELL_NPC_DIALOG,
                failure_reason="Khong mo duoc popup chon loai ban",
                already_success_predicate=lambda current_context, current_frame: (
                    is_npc_dialog_visible(current_context, current_frame)
                    or is_sell_produce_option_visible(current_context, current_frame)
                ),
                session_done_if_exhausted=False,
            )
        elif context.state == BotState.ADVANCE_SELL_NPC_DIALOG:
            handle_navigation_step(
                context,
                action_name="npc_dialog_continue",
                success_state=BotState.OPEN_SELL_PRODUCE_OPTION,
                failure_reason="Khong vuot qua duoc hoi thoai NPC",
                already_success_predicate=is_sell_produce_option_visible,
                session_done_if_exhausted=False,
            )
        elif context.state == BotState.OPEN_SELL_PRODUCE_OPTION:
            handle_navigation_step(
                context,
                action_name="sell_produce_option",
                success_state=BotState.SELL_AUTO_SELECT,
                failure_reason="Khong mo duoc giao dien 'Ban nong san'",
                already_success_predicate=is_sell_auto_select_visible,
                session_done_if_exhausted=False,
            )
        elif context.state == BotState.SELL_AUTO_SELECT:
            handle_sell_auto_select(context)
        elif context.state == BotState.SELL_SUBMIT_SELECTION:
            handle_sell_submit_selection(context)
        elif context.state == BotState.SELL_CONFIRM_POPUP_SUBMIT:
            handle_navigation_step(
                context,
                action_name="sell_popup_submit",
                success_state=BotState.SELL_FINAL_CONFIRM,
                failure_reason="Khong chuyen sang popup xac nhan cuoi cung",
                already_success_predicate=is_sell_final_confirm_visible,
                session_done_if_exhausted=False,
            )
        elif context.state == BotState.SELL_FINAL_CONFIRM:
            handle_navigation_step(
                context,
                action_name="sell_final_confirm",
                success_state=BotState.SELL_SUCCESS_OK,
                failure_reason="Khong ban duoc sau popup xac nhan cuoi cung",
                already_success_predicate=is_sell_success_ok_visible,
                session_done_if_exhausted=False,
            )
        elif context.state == BotState.SELL_SUCCESS_OK:
            handle_navigation_step(
                context,
                action_name="sell_success_ok",
                success_state=BotState.SELL_CLOSE_SCREEN,
                failure_reason="Khong dong duoc popup ban thanh cong",
                session_done_if_exhausted=False,
            )
        elif context.state == BotState.SELL_CLOSE_SCREEN:
            handle_navigation_step(
                context,
                action_name="sell_screen_close",
                success_state=BotState.SELL_DISMISS_END_DIALOG,
                failure_reason="Khong dong duoc giao dien ban nong san",
                already_success_predicate=lambda current_context, current_frame: (
                    is_npc_dialog_visible(current_context, current_frame)
                    or is_sell_cart_visible(current_context, current_frame)
                ),
                session_done_if_exhausted=False,
            )
        elif context.state == BotState.SELL_DISMISS_END_DIALOG:
            handle_sell_end_dialog(context)
        elif context.state == BotState.SESSION_DONE:
            log("Hoan tat quy trinh tu dong.")
            if context.config.get("workflow", {}).get("stop_after_sell", True):
                break
            if context.restart_harvest_immediately:
                context.sell_flow_active = False
                delay_range = context.config.get("workflow", {}).get(
                    "restart_after_sell_delay_seconds",
                    [0.8, 1.6],
                )
                sleep_random(delay_range)
                log("Ban xong. Bat dau lai vong thu hoach moi ngay.")
                begin_harvest_session(context)
                continue
            finish_harvest_session(context, "Hoan tat session")


def main() -> None:
    capture = None

    try:
        config_path = Path(__file__).resolve().parent / "config.json"
        config = load_config(str(config_path))
        detector = TemplateDetector(config)
        background_config = config.get("background", {})
        adb_client = BlueStacksAdb(background_config)
        capture = ScreenCapture(background_config, adb_client)
        mouse = MouseController(config["input"], background_config, adb_client)
        context = BotContext(
            config=config,
            detector=detector,
            capture=capture,
            mouse=mouse,
            reference_client_size=get_reference_client_size(config),
        )
        if background_config.get("enabled", False):
            mode = str(background_config.get("mode", "adb_first"))
            log(f"ADB {mode} da san sang. {adb_client.describe()}")

        run_state_machine(context)
    except KeyboardInterrupt:
        log("Bot dung boi nguoi dung.")
    except Exception as exc:
        log(f"Loi nghiem trong: {exc}")
        traceback.print_exc()
    finally:
        if capture is not None:
            capture.close()
        log("Da tat bot.")


if __name__ == "__main__":
    main()
