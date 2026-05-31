import subprocess
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


class BlueStacksAdb:
    def __init__(self, config: dict) -> None:
        self.device = str(config.get("device", "127.0.0.1:5555"))
        self.timeout_seconds = float(config.get("timeout_seconds", 5.0))
        self.adb_path = self._find_adb(config.get("adb_path"))

    def _find_adb(self, configured_path: Optional[str]) -> Optional[Path]:
        candidates = [
            configured_path,
            r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
            r"C:\Program Files\BlueStacks\HD-Adb.exe",
            r"C:\Program Files (x86)\BlueStacks_nxt\HD-Adb.exe",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return Path(candidate)
        return None

    def is_available(self) -> bool:
        if self.adb_path is None:
            return False
        self._run(["connect", self.device], check=False)
        result = self._run(["-s", self.device, "shell", "echo", "ready"], check=False)
        return result is not None and result.returncode == 0 and b"ready" in result.stdout

    def capture(self) -> Optional[np.ndarray]:
        result = self._run(
            ["-s", self.device, "exec-out", "screencap", "-p"],
            check=False,
        )
        if result is None or result.returncode != 0 or not result.stdout:
            return None
        encoded = np.frombuffer(result.stdout, dtype=np.uint8)
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)

    def tap(self, x_pos: int, y_pos: int) -> bool:
        result = self._run(
            ["-s", self.device, "shell", "input", "tap", str(x_pos), str(y_pos)],
            check=False,
        )
        return result is not None and result.returncode == 0

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 350,
    ) -> bool:
        result = self._run(
            [
                "-s",
                self.device,
                "shell",
                "input",
                "swipe",
                str(start_x),
                str(start_y),
                str(end_x),
                str(end_y),
                str(duration_ms),
            ],
            check=False,
        )
        return result is not None and result.returncode == 0

    def _run(self, args: list[str], check: bool) -> Optional[subprocess.CompletedProcess]:
        if self.adb_path is None:
            return None
        try:
            return subprocess.run(
                [str(self.adb_path), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=check,
            )
        except (OSError, subprocess.SubprocessError):
            return None
