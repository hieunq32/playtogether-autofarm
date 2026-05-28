import ctypes
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from utils.config import load_config
from utils.geometry import get_reference_client_size
from utils.logger import log
from utils.window import GameWindow, find_bluestacks_window


VK_ESCAPE = 0x1B
VK_F6 = 0x75


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def is_key_pressed(vk_code: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)


def get_mouse_position() -> Tuple[int, int]:
    point = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def find_window_from_config(config: dict) -> Optional[GameWindow]:
    return find_bluestacks_window(config["window"]["title_keywords"])


def clear_status_line() -> None:
    sys.stdout.write("\r" + (" " * 220) + "\r")
    sys.stdout.flush()


def print_capture_snippets(
    relative_x: int,
    relative_y: int,
    window: GameWindow,
    reference_size: Optional[Tuple[int, int]],
) -> None:
    x_ratio = relative_x / max(window.width, 1)
    y_ratio = relative_y / max(window.height, 1)

    print("point_pixel:")
    print(f"[{relative_x}, {relative_y}]")
    print()
    print("point_ratio:")
    print(f"[{x_ratio:.6f}, {y_ratio:.6f}]")
    print()
    if reference_size is not None:
        reference_width, reference_height = reference_size
        scaled_x = round(x_ratio * reference_width)
        scaled_y = round(y_ratio * reference_height)
        print("point_scaled_for_reference:")
        print(f"[{scaled_x}, {scaled_y}]")
        print()
    print("fallback_click_ratio snippet:")
    print("{")
    print(f'  "fallback_click_ratio": [{x_ratio:.6f}, {y_ratio:.6f}]')
    print("}")
    print()
    print("scroll_hover_point_ratio snippet:")
    print("{")
    print(f'  "hover_point_ratio": [{x_ratio:.6f}, {y_ratio:.6f}]')
    print("}")
    print(flush=True)
    print("generic point snippet:")
    print("{")
    print(f'  "x_ratio": {x_ratio:.6f},')
    print(f'  "y_ratio": {y_ratio:.6f}')
    print("}")
    print(flush=True)


def print_instructions() -> None:
    log("Coordinate helper da chay.")
    log("Dat chuot vao vi tri can lay trong BlueStacks.")
    log("Nhan F6 de in toa do pixel, ratio va snippet mau cho config.json.")
    log("Nhan ESC de thoat.")


def main() -> None:
    config_path = Path(__file__).resolve().parent / "config.json"
    config = load_config(str(config_path))
    reference_size = get_reference_client_size(config)
    print_instructions()

    window: Optional[GameWindow] = None
    last_search_time = 0.0
    last_logged_bounds = None
    f6_was_pressed = False

    while True:
        if is_key_pressed(VK_ESCAPE):
            clear_status_line()
            log("Da thoat coordinate helper.")
            break

        now = time.monotonic()
        if window is None or now - last_search_time >= 1.0:
            window = find_window_from_config(config)
            last_search_time = now

            if window is not None and window.bounds != last_logged_bounds:
                last_logged_bounds = window.bounds
                log(
                    "BlueStacks client area: "
                    f"left={window.left}, top={window.top}, "
                    f"width={window.width}, height={window.height}"
                )

        if window is None or window.width <= 0 or window.height <= 0:
            sys.stdout.write(
                "\rKhong tim thay cua so BlueStacks hop le. "
                "Hay mo va hien cua so game..."
            )
            sys.stdout.flush()
            time.sleep(0.1)
            continue

        mouse_x, mouse_y = get_mouse_position()
        inside_window = (
            window.left <= mouse_x < window.right
            and window.top <= mouse_y < window.bottom
        )

        if inside_window:
            relative_x = mouse_x - window.left
            relative_y = mouse_y - window.top
            status = (
                f"\rWindow {window.width}x{window.height} | "
                f"Abs=({mouse_x}, {mouse_y}) | "
                f"Rel=({relative_x}, {relative_y})"
            )
        else:
            relative_x = None
            relative_y = None
            status = (
                f"\rWindow {window.width}x{window.height} | "
                f"Abs=({mouse_x}, {mouse_y}) | "
                "Chuot dang o ngoai vung game"
            )

        sys.stdout.write(status.ljust(220)[:220])
        sys.stdout.flush()

        f6_is_pressed = is_key_pressed(VK_F6)
        if f6_is_pressed and not f6_was_pressed:
            clear_status_line()
            if not inside_window or relative_x is None or relative_y is None:
                log("Hay dua chuot vao trong vung game roi bam F6.")
            else:
                log(
                    f"Da ghi diem tai relative=({relative_x}, {relative_y}) "
                    f"absolute=({mouse_x}, {mouse_y})"
                )
                print_capture_snippets(relative_x, relative_y, window, reference_size)

        f6_was_pressed = f6_is_pressed
        time.sleep(0.05)


if __name__ == "__main__":
    main()
