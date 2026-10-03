import json
from pathlib import Path

import main


def test_premium_watering_tool_can_override_buy_button_retries():
    target = {
        "name": "watering_premium",
        "buy_button_retry_attempts": 2,
    }
    config = {"workflow": {"tool_buy_button_retry_attempts": 6}}

    assert main.get_tool_buy_button_retry_attempts(target, config) == 2


def test_tool_buy_retry_keeps_global_default_for_other_tools():
    target = {"name": "watering_basic"}
    config = {"workflow": {"tool_buy_button_retry_attempts": 6}}

    assert main.get_tool_buy_button_retry_attempts(target, config) == 6


def test_watering_basic_uses_two_buy_button_attempts_from_project_config():
    project_root = Path(__file__).resolve().parents[1]
    config = json.loads((project_root / "config.json").read_text(encoding="utf-8"))
    target = next(
        item
        for item in config["workflow"]["tool_purchase_targets"]
        if item["name"] == "watering_basic"
    )

    assert main.get_tool_buy_button_retry_attempts(target, config) == 2
