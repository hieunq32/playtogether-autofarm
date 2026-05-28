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
        if session_done_if_exhausted:
            finish_harvest_session(context, failure_reason)
        else:
            log(f"Het so lan thu cho state '{context.state.name}'.")
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


def sync_state_from_visible_screen(context: BotContext, frame) -> bool:
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

    if context.config["workflow"].get("stop_when_bag_full", True):
        raise KeyboardInterrupt

    finish_harvest_session(context, "Day tui")


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
        elif context.state == BotState.SESSION_DONE:
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
