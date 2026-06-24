from dataclasses import dataclass
from typing import Iterable, Optional

import win32con
import win32gui


@dataclass
class GameWindow:
    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def bounds(self):
        return self.left, self.top, self.width, self.height


def _keyword_match(title: str, keywords: Iterable[str]) -> bool:
    lowered_title = title.lower()
    return any(keyword.lower() in lowered_title for keyword in keywords)


def _get_client_area(hwnd: int) -> Optional[GameWindow]:
    if not win32gui.IsWindowVisible(hwnd):
        return None

    title = win32gui.GetWindowText(hwnd)
    if not title:
        return None

    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    if right <= left or bottom <= top:
        return None

    screen_left, screen_top = win32gui.ClientToScreen(hwnd, (left, top))
    width = right - left
    height = bottom - top
    return GameWindow(
        hwnd=hwnd,
        title=title,
        left=screen_left,
        top=screen_top,
        width=width,
        height=height,
    )


def find_bluestacks_window(title_keywords) -> Optional[GameWindow]:
    candidates = []

    def enum_callback(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if title and _keyword_match(title, title_keywords):
            window = _get_client_area(hwnd)
            if window is not None:
                candidates.append(window)
        return True

    win32gui.EnumWindows(enum_callback, None)

    if not candidates:
        return None

    candidates.sort(key=lambda item: item.width * item.height, reverse=True)
    return candidates[0]


def resize_window_client_area(
    window: GameWindow,
    target_width: int,
    target_height: int,
    tolerance_pixels: int = 3,
) -> Optional[GameWindow]:
    if target_width <= 0 or target_height <= 0:
        return None

    if (
        abs(window.width - target_width) <= tolerance_pixels
        and abs(window.height - target_height) <= tolerance_pixels
    ):
        return window

    if win32gui.IsIconic(window.hwnd):
        win32gui.ShowWindow(window.hwnd, win32con.SW_RESTORE)

    outer_left, outer_top, outer_right, outer_bottom = win32gui.GetWindowRect(window.hwnd)
    client_left, client_top, client_right, client_bottom = win32gui.GetClientRect(window.hwnd)
    current_client_width = max(1, client_right - client_left)
    current_client_height = max(1, client_bottom - client_top)
    outer_width = max(1, outer_right - outer_left)
    outer_height = max(1, outer_bottom - outer_top)

    border_width = outer_width - current_client_width
    border_height = outer_height - current_client_height
    target_outer_width = max(1, target_width + border_width)
    target_outer_height = max(1, target_height + border_height)

    win32gui.SetWindowPos(
        window.hwnd,
        None,
        outer_left,
        outer_top,
        target_outer_width,
        target_outer_height,
        win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
    )
    return _get_client_area(window.hwnd)


def is_window_foreground(window: GameWindow) -> bool:
    return win32gui.GetForegroundWindow() == window.hwnd


def activate_window(window: GameWindow) -> None:
    if win32gui.IsIconic(window.hwnd):
        win32gui.ShowWindow(window.hwnd, win32con.SW_RESTORE)

    win32gui.BringWindowToTop(window.hwnd)
    try:
        win32gui.SetForegroundWindow(window.hwnd)
    except win32gui.error:
        # Windows may deny foreground changes for background processes.
        pass
