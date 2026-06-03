import subprocess
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


class BlueStacksAdb:
    def __init__(self, config: dict) -> None:
        self.device = str(config.get("device", "127.0.0.1:5555"))
        self.timeout_seconds = float(config.get("timeout_seconds", 5.0))
        self.connect_retries = int(config.get("connect_retries", 2))
        self.adb_path = self._find_adb(config.get("adb_path"))
        self.last_error = ""

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
        return self.ensure_connected()

    def ensure_connected(self) -> bool:
        if self.adb_path is None:
            self.last_error = "Khong tim thay HD-Adb.exe."
            return False

        for _ in range(max(1, self.connect_retries)):
            self._run(["connect", self.device], check=False)
            result = self._run(["-s", self.device, "shell", "echo", "ready"], check=False)
            if result is not None and result.returncode == 0 and b"ready" in result.stdout:
                self.last_error = ""
                return True
            if result is not None:
                self.last_error = self._format_process_error(result)

        if not self.last_error:
            self.last_error = "ADB khong phan hoi."
        return False

    def capture(self) -> Optional[np.ndarray]:
        if not self.ensure_connected():
            return None

        result = self._run(
            ["-s", self.device, "exec-out", "screencap", "-p"],
            check=False,
        )
        if result is None or result.returncode != 0 or not result.stdout:
            if result is not None:
                self.last_error = self._format_process_error(result)
            return None
        encoded = np.frombuffer(result.stdout, dtype=np.uint8)
        frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if frame is None:
            self.last_error = "Khong decode duoc frame screencap tu ADB."
            return None
        self.last_error = ""
        return frame

    def tap(self, x_pos: int, y_pos: int) -> bool:
        if not self.ensure_connected():
            return False

        result = self._run(
            ["-s", self.device, "shell", "input", "tap", str(x_pos), str(y_pos)],
            check=False,
        )
        ok = result is not None and result.returncode == 0
        if not ok and result is not None:
            self.last_error = self._format_process_error(result)
        return ok

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 350,
    ) -> bool:
        if not self.ensure_connected():
            return False

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
        ok = result is not None and result.returncode == 0
        if not ok and result is not None:
            self.last_error = self._format_process_error(result)
        return ok

    def describe(self) -> str:
        path = str(self.adb_path) if self.adb_path is not None else "not found"
        return f"adb_path={path}, device={self.device}"

    def _run(self, args: list[str], check: bool) -> Optional[subprocess.CompletedProcess]:
        if self.adb_path is None:
            self.last_error = "Khong tim thay HD-Adb.exe."
            return None
        try:
            return subprocess.run(
                [str(self.adb_path), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=check,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.last_error = str(exc)
            return None

    def _format_process_error(self, result: subprocess.CompletedProcess) -> str:
        stderr = result.stderr.decode("utf-8", errors="ignore").strip()
        stdout = result.stdout.decode("utf-8", errors="ignore").strip()
        detail = stderr or stdout or f"returncode={result.returncode}"
        return detail
