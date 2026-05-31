import cv2
import numpy as np
from mss import mss

from utils.adb import BlueStacksAdb


class ScreenCapture:
    def __init__(self, background_config: dict | None = None) -> None:
        self._capture = mss()
        self.background_config = background_config or {}
        self.adb = BlueStacksAdb(self.background_config)
        self.background_enabled = bool(self.background_config.get("enabled", False))
        self.background_active = self.background_enabled and self.adb.is_available()

    def grab(self, window):
        if self.background_active:
            frame = self._grab_adb_frame(window)
            if frame is not None:
                return frame
            raise RuntimeError(
                "Mat ket noi ADB khi dang chay background. "
                "Bot dung an toan thay vi doc nham man hinh desktop."
            )

        monitor = {
            "left": window.left,
            "top": window.top,
            "width": window.width,
            "height": window.height,
        }
        frame = np.array(self._capture.grab(monitor))
        return frame[:, :, :3]

    def _grab_adb_frame(self, window):
        adb_frame = self.adb.capture()
        if adb_frame is None:
            return None

        render_ratio = self.background_config.get("render_region_ratio", [0.0, 0.0, 1.0, 1.0])
        left = round(window.width * float(render_ratio[0]))
        top = round(window.height * float(render_ratio[1]))
        right = round(window.width * float(render_ratio[2]))
        bottom = round(window.height * float(render_ratio[3]))
        render_width = max(1, right - left)
        render_height = max(1, bottom - top)

        canvas = np.zeros((window.height, window.width, 3), dtype=np.uint8)
        canvas[top:bottom, left:right] = cv2.resize(adb_frame, (render_width, render_height))
        return canvas

    def close(self) -> None:
        self._capture.close()
