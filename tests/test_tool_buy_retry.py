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
