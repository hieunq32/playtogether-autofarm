from typing import Optional, Sequence, Tuple


Size = Tuple[int, int]
Point = Tuple[int, int]


def get_reference_client_size(config: dict) -> Optional[Size]:
    reference = config.get("window", {}).get("reference_client_size")
    if not reference:
        return None

    width = int(reference.get("width", 0))
    height = int(reference.get("height", 0))
    if width <= 0 or height <= 0:
        return None
    return width, height


def clamp_point(point: Point, current_size: Size) -> Point:
    width, height = current_size
    max_x = max(width - 1, 0)
    max_y = max(height - 1, 0)
    return (
        max(0, min(max_x, int(point[0]))),
        max(0, min(max_y, int(point[1]))),
    )


def _scale_from_reference(
    x_value: float,
    y_value: float,
    current_size: Size,
    reference_size: Optional[Size],
) -> Point:
    current_width, current_height = current_size
    if reference_size is None:
        return clamp_point((round(x_value), round(y_value)), current_size)

    reference_width, reference_height = reference_size
    scaled_x = x_value * current_width / reference_width
    scaled_y = y_value * current_height / reference_height
    return clamp_point((round(scaled_x), round(scaled_y)), current_size)


def resolve_point(
    point_config: dict,
    current_size: Size,
    reference_size: Optional[Size] = None,
) -> Point:
    if "x_ratio" in point_config and "y_ratio" in point_config:
        width, height = current_size
        x_value = float(point_config["x_ratio"]) * width
        y_value = float(point_config["y_ratio"]) * height
        return clamp_point((round(x_value), round(y_value)), current_size)

    if "x" in point_config and "y" in point_config:
        return _scale_from_reference(
            float(point_config["x"]),
            float(point_config["y"]),
            current_size,
            reference_size,
        )

    raise KeyError("Point config must contain x/y or x_ratio/y_ratio.")


def resolve_click_position(
    button_config: dict,
    current_size: Size,
    reference_size: Optional[Size] = None,
) -> Optional[Point]:
    ratio_values = button_config.get("fallback_click_ratio")
    if isinstance(ratio_values, Sequence) and len(ratio_values) == 2:
        ratio_point = {
            "x_ratio": float(ratio_values[0]),
            "y_ratio": float(ratio_values[1]),
        }
        return resolve_point(ratio_point, current_size, reference_size)

    pixel_values = button_config.get("fallback_click")
    if isinstance(pixel_values, Sequence) and len(pixel_values) == 2:
        pixel_point = {
            "x": float(pixel_values[0]),
            "y": float(pixel_values[1]),
        }
        return resolve_point(pixel_point, current_size, reference_size)

    return None


def resolve_click_offset(
    button_config: dict,
    current_size: Size,
    reference_size: Optional[Size] = None,
) -> Point:
    ratio_values = button_config.get("click_offset_ratio")
    if isinstance(ratio_values, Sequence) and len(ratio_values) == 2:
        width, height = current_size
        return (
            round(float(ratio_values[0]) * width),
            round(float(ratio_values[1]) * height),
        )

    pixel_values = button_config.get("click_offset")
    if isinstance(pixel_values, Sequence) and len(pixel_values) == 2:
        if reference_size is None:
            return round(float(pixel_values[0])), round(float(pixel_values[1]))

        reference_width, reference_height = reference_size
        current_width, current_height = current_size
        return (
            round(float(pixel_values[0]) * current_width / reference_width),
            round(float(pixel_values[1]) * current_height / reference_height),
        )

    return 0, 0


def resolve_radius(
    point_config: dict,
    current_size: Size,
    reference_size: Optional[Size] = None,
    default_radius: int = 45,
) -> int:
    width, height = current_size
    min_dimension = max(min(width, height), 1)

    if "radius_ratio" in point_config:
        radius = round(float(point_config["radius_ratio"]) * min_dimension)
        return max(1, radius)

    radius_value = float(point_config.get("radius", default_radius))
    if reference_size is None:
        return max(1, round(radius_value))

    reference_width, reference_height = reference_size
    scale = min(width / reference_width, height / reference_height)
    return max(1, round(radius_value * scale))
