import ctypes


VK_ESCAPE = 0x1B


def is_escape_pressed() -> bool:
    """Tra ve True neu phim ESC dang duoc nhan."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
