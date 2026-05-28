import random
import ctypes

from utils.timing import random_in_range


class MouseController:
    def __init__(self, input_config: dict) -> None:
        self.backend_name = input_config.get("backend", "pydirectinput").lower()
        self.move_duration = input_config.get("move_duration_seconds", [0.04, 0.12])
        self.click_jitter = int(input_config.get("click_jitter_pixels", 0))
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
        absolute_x, absolute_y = self.move_relative(window, relative_point)
        ctypes.windll.user32.SetCursorPos(absolute_x, absolute_y)
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(wheel_delta), 0)
