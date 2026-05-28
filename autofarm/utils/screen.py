import numpy as np
from mss import mss


class ScreenCapture:
    def __init__(self) -> None:
        self._capture = mss()

    def grab(self, window):
        monitor = {
            "left": window.left,
            "top": window.top,
            "width": window.width,
            "height": window.height,
        }
        frame = np.array(self._capture.grab(monitor))
        return frame[:, :, :3]

    def close(self) -> None:
        self._capture.close()
