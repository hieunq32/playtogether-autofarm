import subprocess
from types import SimpleNamespace

import main
from utils.adb import BlueStacksAdb


def _completed(returncode, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_adb_swipe_retries_once_after_transient_command_failure(monkeypatch):
    adb = BlueStacksAdb({"swipe_retries": 1})
    results = iter([_completed(1), _completed(0)])
    commands = []
    monkeypatch.setattr(adb, "ensure_connected", lambda: True)
    monkeypatch.setattr(
        adb,
        "_run",
        lambda args, check: commands.append(args) or next(results),
    )

    assert adb.swipe(1200, 430, 1200, 850, duration_ms=350)
    assert len(commands) == 2
    assert all(
        command[3:]
        == ["input", "swipe", "1200", "430", "1200", "850", "350"]
        for command in commands
    )
    assert adb.last_error == ""


def test_adb_swipe_reports_attempt_count_and_command_when_all_retries_fail(monkeypatch):
    adb = BlueStacksAdb({"swipe_retries": 1})
    commands = []
    monkeypatch.setattr(adb, "ensure_connected", lambda: True)
    monkeypatch.setattr(
        adb,
        "_run",
        lambda args, check: commands.append(args) or _completed(1),
    )

    assert not adb.swipe(1200, 430, 1200, 850, duration_ms=350)
    assert len(commands) == 2
    assert "2 attempts" in adb.last_error
    assert "returncode=1" in adb.last_error
    assert "1200,430 -> 1200,850" in adb.last_error


def test_harvest_scroll_failure_pauses_session_instead_of_escaping(monkeypatch):
    calls = []

    class Mouse:
        def scroll_relative(self, *args):
            calls.append("swipe")
            raise RuntimeError("ADB swipe failed")

    context = SimpleNamespace(
        window=object(),
        config={
            "navigation": {"scroll": {"down_wheel_delta": -420}},
            "background": {"scroll_distance_pixels": 420},
        },
        mouse=Mouse(),
    )
    monkeypatch.setattr(main, "get_scroll_hover_point", lambda _: (100, 100))
    monkeypatch.setattr(
        main,
        "finish_harvest_session",
        lambda _, reason: calls.append(("pause", reason)),
    )
    monkeypatch.setattr(main, "log", lambda *_: None)

    main.scroll_harvest_list(context, count=2, reason="locked crop", delay_range=[0, 0])

    assert calls[0] == "swipe"
    assert calls[1][0] == "pause"
    assert len(calls) == 2
