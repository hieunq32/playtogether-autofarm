import random
import ctypes

from utils.adb import BlueStacksAdb
from utils.timing import random_in_range


class MouseController:
    def __init__(self, input_config: dict, background_config: dict | None = None) -> None:
        self.backend_name = input_config.get("backend", "pydirectinput").lower()
        self.move_duration = input_config.get("move_duration_seconds", [0.04, 0.12])
        self.click_jitter = int(input_config.get("click_jitter_pixels", 0))
        self.background_config = background_config or {}
        self.adb = BlueStacksAdb(self.background_config)
        self.background_enabled = bool(self.background_config.get("enabled", False))
        self.background_active = self.background_enabled and self.adb.is_available()
        self.backend = self._load_backend()

    def _load_backend(self):
        if self.backend_name == "pyautogui":
            import pyautogui

            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = 0
            return pyautogui

        import pydirectinput

        pydirectinput.FAILSAFE = False
        pydirectinput.PAUSE = 0
        return pydirectinput

    def click_relative(self, window, relative_point) -> None:
        """Click theo toa do tuong doi trong client area cua BlueStacks."""
        if self.background_active:
            adb_point = self._relative_to_adb(window, relative_point)
            self.adb.tap(*adb_point)
            return

        relative_x, relative_y = relative_point
        absolute_x = window.left + relative_x
        absolute_y = window.top + relative_y

        if self.click_jitter > 0:
            absolute_x += random.randint(-self.click_jitter, self.click_jitter)
            absolute_y += random.randint(-self.click_jitter, self.click_jitter)

        absolute_x = max(window.left, min(window.right - 1, absolute_x))
        absolute_y = max(window.top, min(window.bottom - 1, absolute_y))

        move_duration = random_in_range(self.move_duration)
        self.backend.moveTo(absolute_x, absolute_y, duration=move_duration)
        self.backend.click(x=absolute_x, y=absolute_y, button="left")

    def move_relative(self, window, relative_point) -> tuple[int, int]:
        relative_x, relative_y = relative_point
        absolute_x = max(window.left, min(window.right - 1, window.left + relative_x))
        absolute_y = max(window.top, min(window.bottom - 1, window.top + relative_y))
        move_duration = random_in_range(self.move_duration)
        self.backend.moveTo(absolute_x, absolute_y, duration=move_duration)
        return absolute_x, absolute_y

    def scroll_relative(self, window, relative_point, wheel_delta: int) -> None:
        if self.background_active:
            start_x, start_y = self._relative_to_adb(window, relative_point)
            distance = int(self.background_config.get("scroll_distance_pixels", 420))
            direction = -1 if wheel_delta < 0 else 1
            adb_height = int(self.background_config.get("adb_screen_size", [1600, 900])[1])
            end_y = max(0, min(adb_height - 1, start_y + direction * distance))
            self.adb.swipe(start_x, start_y, start_x, end_y)
            return

        absolute_x, absolute_y = self.move_relative(window, relative_point)
        ctypes.windll.user32.SetCursorPos(absolute_x, absolute_y)
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(wheel_delta), 0)

    def _relative_to_adb(self, window, relative_point) -> tuple[int, int]:
        render_ratio = self.background_config.get("render_region_ratio", [0.0, 0.0, 1.0, 1.0])
        adb_size = self.background_config.get("adb_screen_size", [1600, 900])
        left = window.width * float(render_ratio[0])
        top = window.height * float(render_ratio[1])
        width = window.width * (float(render_ratio[2]) - float(render_ratio[0]))
        height = window.height * (float(render_ratio[3]) - float(render_ratio[1]))
        x_pos = round((float(relative_point[0]) - left) * float(adb_size[0]) / max(1.0, width))
        y_pos = round((float(relative_point[1]) - top) * float(adb_size[1]) / max(1.0, height))
        return (
            max(0, min(int(adb_size[0]) - 1, x_pos)),
            max(0, min(int(adb_size[1]) - 1, y_pos)),
        )
