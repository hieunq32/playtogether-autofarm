import json
from pathlib import Path


def _resolve_path(project_dir: Path, value: str) -> str:
    return str((project_dir / value).resolve())


def _resolve_template_paths(config: dict, project_dir: Path) -> None:
    for template in config.get("fruit_templates", []):
        template["path"] = _resolve_path(project_dir, template["path"])
        if "template_candidates" in template:
            template["template_candidates"] = [
                _resolve_path(project_dir, value)
                for value in template.get("template_candidates", [])
            ]

    buttons = config.get("navigation", {}).get("buttons", {})
    for button_config in buttons.values():
        template_path = button_config.get("template")
        if template_path:
            button_config["template"] = _resolve_path(project_dir, template_path)
        if "template_candidates" in button_config:
            button_config["template_candidates"] = [
                _resolve_path(project_dir, value)
                for value in button_config.get("template_candidates", [])
            ]

    messages = config.get("messages", {})
    for message_config in messages.values():
        template_path = message_config.get("template")
        if template_path:
            message_config["template"] = _resolve_path(project_dir, template_path)


def load_config(config_path: str = "config.json") -> dict:
    resolved_config_path = Path(config_path).resolve()
    project_dir = resolved_config_path.parent

    with open(resolved_config_path, "r", encoding="utf-8") as file:
        config = json.load(file)

    _resolve_template_paths(config, project_dir)
    return config
