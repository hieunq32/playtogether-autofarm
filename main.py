import time
import traceback
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Optional

import cv2

from utils.config import load_config
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
    restart_harvest_immediately: bool = False
    sell_flow_active: bool = False
    startup_flow_bootstrapped: bool = False


def set_state(context: BotContext, new_state: BotState) -> None:
    if context.state != new_state:
        log(f"State -> {new_state.name}")
        context.state = new_state
        context.state_attempts = 0


def refresh_game_window(context: BotContext) -> bool:
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


def save_runtime_frame(filename: str, frame) -> None:
    output_path = Path(__file__).resolve().parent / filename
    cv2.imwrite(str(output_path), frame)
    log(f"Da luu anh runtime: {output_path.name}")


def reset_session(context: BotContext) -> None:
    context.active_row = None
    context.scroll_attempts = 0
    context.no_match_search_attempts = 0
    context.state_attempts = 0
    context.session_harvest_count = 0
    context.restart_harvest_immediately = False
    context.sell_flow_active = False


def schedule_next_check(context: BotContext, reason: str) -> None:
    wait_seconds = random_in_range(context.config["scheduler"]["idle_check_interval_seconds"])
    context.next_check_time = time.monotonic() + wait_seconds
    log(f"{reason}. Lan kiem tra tiep theo sau {wait_seconds:.1f} giay.")


def begin_harvest_session(context: BotContext) -> None:
    reset_session(context)
    set_state(context, BotState.OPEN_HOME)


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


def sync_state_from_visible_screen(context: BotContext, frame) -> bool:
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

    should_sync_sell = context.sell_flow_active or context.state in sell_states

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
    return False


def handle_search_target_rows(context: BotContext) -> None:
    frame = capture_frame(context)

    if context.detector.find_message(frame, "bag_full") is not None:
        set_state(context, BotState.BAG_FULL)
        return

    rows = context.detector.find_harvestable_rows(frame)
    if rows:
        context.no_match_search_attempts = 0
        context.active_row = rows[0]
        set_state(context, BotState.HARVEST_ROW)
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
    save_runtime_frame("bag_full_live_detected.png", frame)
    log("Phat hien thong bao day tui. Thu dong popup bang nut X.")

    if click_named_button(context, "close_harvest_popup", frame):
        sleep_random(context.config["timing"]["post_navigation_wait_seconds"])
        closed_frame = capture_frame(context)
        save_runtime_frame("after_bag_full_close.png", closed_frame)
    else:
        log("Khong dong duoc popup day tui bang nut X.")

    workflow_config = context.config.get("workflow", {})
    if workflow_config.get("sell_when_bag_full", True):
        log("Bat dau quy trinh ban nong san sau khi day tui.")
        context.sell_flow_active = True
        set_state(context, BotState.OPEN_SELL_ENTRY)
        return

    if workflow_config.get("stop_when_bag_full", True):
        set_state(context, BotState.SESSION_DONE)
        return

    finish_harvest_session(context, "Day tui")


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
            )
        elif context.state == BotState.OPEN_AVAILABLE_TO_HARVEST:
            handle_navigation_step(
                context,
                action_name="available_to_harvest",
                success_state=BotState.OPEN_HARVEST_POPUP,
                failure_reason="Khong thay 'Co the thu hoach'. Co the chua den gio thu hoach",
                already_success_predicate=is_harvest_popup_visible,
            )
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
        elif context.state == BotState.OPEN_SELL_ENTRY:
            handle_navigation_step(
                context,
                action_name="sell_entry",
                success_state=BotState.OPEN_SELL_CART,
                failure_reason="Khong mo duoc giao dien ban",
                already_success_predicate=is_sell_cart_visible,
                session_done_if_exhausted=False,
                retry_forever=True,
            )
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
    config_path = Path(__file__).resolve().parent / "config.json"
    config = load_config(str(config_path))
    detector = TemplateDetector(config)
    capture = ScreenCapture()
    mouse = MouseController(config["input"])
    context = BotContext(
        config=config,
        detector=detector,
        capture=capture,
        mouse=mouse,
        reference_client_size=get_reference_client_size(config),
    )

    try:
        run_state_machine(context)
    except KeyboardInterrupt:
        log("Bot dung boi nguoi dung.")
    except Exception as exc:
        log(f"Loi nghiem trong: {exc}")
        traceback.print_exc()
    finally:
        capture.close()
        log("Da tat bot.")


if __name__ == "__main__":
    main()
