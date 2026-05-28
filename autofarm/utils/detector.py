from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from utils.logger import log


@dataclass
class LoadedTemplate:
    name: str
    path: str
    threshold: float
    image: np.ndarray
    width: int
    height: int
    search_region_ratio: Optional[Tuple[float, float, float, float]] = None


@dataclass
class MatchResult:
    template_name: str
    score: float
    top_left: Tuple[int, int]
    click_point: Tuple[int, int]


@dataclass
class HarvestRowMatch:
    fruit_name: str
    label_point: Tuple[int, int]
    button_point: Tuple[int, int]
    label_score: float
    button_score: float


class TemplateDetector:
    def __init__(self, config: dict) -> None:
        self.default_threshold = config["matching"].get("default_threshold", 0.85)
        self.match_distance = config["matching"].get("match_distance", 25)
        self.row_tolerance_ratio = config["matching"].get("row_tolerance_ratio", 0.06)
        self.template_scales = [
            float(scale) for scale in config["matching"].get("template_scales", [1.0])
        ]
        self.fruit_templates = self._load_templates(config.get("fruit_templates", []))
        self.action_templates = self._load_action_templates(
            config.get("navigation", {}).get("buttons", {})
        )
        self.message_templates = self._load_message_templates(config.get("messages", {}))

    def _load_templates(self, template_items: Iterable[dict]) -> List[LoadedTemplate]:
        loaded_templates: List[LoadedTemplate] = []

        for item in template_items:
            if not item.get("enabled", True):
                continue

            image = cv2.imread(item["path"], cv2.IMREAD_GRAYSCALE)
            if image is None:
                log(f"Warning: khong doc duoc template: {item['path']}")
                continue

            height, width = image.shape[:2]
            loaded_templates.append(
                LoadedTemplate(
                    name=item["name"],
                    path=item["path"],
                    threshold=item.get("threshold", self.default_threshold),
                    image=image,
                    width=width,
                    height=height,
                    search_region_ratio=self._parse_search_region(item),
                )
            )

        return loaded_templates

    def _load_action_templates(self, button_config: Dict[str, dict]) -> Dict[str, LoadedTemplate]:
        loaded: Dict[str, LoadedTemplate] = {}

        for action_name, config in button_config.items():
            template_path = config.get("template")
            if not template_path:
                continue

            image = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
            if image is None:
                log(f"Warning: khong doc duoc action template: {template_path}")
                continue

            height, width = image.shape[:2]
            loaded[action_name] = LoadedTemplate(
                name=action_name,
                path=template_path,
                threshold=config.get("threshold", self.default_threshold),
                image=image,
                width=width,
                height=height,
                search_region_ratio=self._parse_search_region(config),
            )

        return loaded

    def _load_message_templates(self, message_config: Dict[str, dict]) -> Dict[str, LoadedTemplate]:
        loaded: Dict[str, LoadedTemplate] = {}

        for message_name, config in message_config.items():
            template_path = config.get("template")
            if not template_path:
                continue

            image = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
            if image is None:
                log(f"Warning: khong doc duoc message template: {template_path}")
                continue

            height, width = image.shape[:2]
            loaded[message_name] = LoadedTemplate(
                name=message_name,
                path=template_path,
                threshold=config.get("threshold", self.default_threshold),
                image=image,
                width=width,
                height=height,
                search_region_ratio=self._parse_search_region(config),
            )

        return loaded

    def _parse_search_region(
        self,
        config: dict,
    ) -> Optional[Tuple[float, float, float, float]]:
        values = config.get("search_region_ratio")
        if not isinstance(values, Sequence) or len(values) != 4:
            return None

        left, top, right, bottom = [float(value) for value in values]
        return left, top, right, bottom

    def find_action_button(self, frame: np.ndarray, action_name: str) -> Optional[MatchResult]:
        template = self.action_templates.get(action_name)
        if template is None:
            return None
        return self._find_first_match(frame, [template])

    def find_all_action_buttons(self, frame: np.ndarray, action_name: str) -> List[MatchResult]:
        template = self.action_templates.get(action_name)
        if template is None:
            return []
        return self._find_all_matches(frame, [template])

    def find_message(self, frame: np.ndarray, message_name: str) -> Optional[MatchResult]:
        template = self.message_templates.get(message_name)
        if template is None:
            return None
        return self._find_first_match(frame, [template])

    def find_harvestable_rows(self, frame: np.ndarray) -> List[HarvestRowMatch]:
        label_matches = self._find_all_matches(frame, self.fruit_templates)
        button_matches = self.find_all_action_buttons(frame, "fruit_harvest")
        if not label_matches or not button_matches:
            return []

        row_tolerance = max(6, round(frame.shape[0] * float(self.row_tolerance_ratio)))
        remaining_buttons = button_matches.copy()
        rows: List[HarvestRowMatch] = []

        for label in sorted(label_matches, key=lambda item: (item.click_point[1], item.click_point[0])):
            candidates = [
                button
                for button in remaining_buttons
                if button.top_left[0] > label.top_left[0]
                and abs(button.top_left[1] - label.top_left[1]) <= row_tolerance
            ]
            if not candidates:
                continue

            button = sorted(
                candidates,
                key=lambda item: (
                    abs(item.top_left[1] - label.top_left[1]),
                    item.top_left[0] - label.top_left[0],
                    -item.score,
                ),
            )[0]
            remaining_buttons.remove(button)
            rows.append(
                HarvestRowMatch(
                    fruit_name=label.template_name,
                    label_point=label.click_point,
                    button_point=button.click_point,
                    label_score=label.score,
                    button_score=button.score,
                )
            )

        return rows

    def _find_all_matches(
        self,
        frame: np.ndarray,
        templates: Sequence[LoadedTemplate],
    ) -> List[MatchResult]:
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        matches: List[MatchResult] = []

        for template in templates:
            search_frame, offset = self._resolve_search_frame(gray_frame, template)
            for scaled_image, scaled_width, scaled_height in self._iter_scaled_templates(template):
                if (
                    search_frame.shape[1] < scaled_width
                    or search_frame.shape[0] < scaled_height
                ):
                    continue

                result = cv2.matchTemplate(search_frame, scaled_image, cv2.TM_CCOEFF_NORMED)
                y_points, x_points = np.where(result >= template.threshold)

                for x_pos, y_pos in zip(x_points, y_points):
                    matches.append(
                        MatchResult(
                            template_name=template.name,
                            score=float(result[y_pos, x_pos]),
                            top_left=(int(x_pos + offset[0]), int(y_pos + offset[1])),
                            click_point=(
                                int(x_pos + offset[0] + scaled_width // 2),
                                int(y_pos + offset[1] + scaled_height // 2),
                            ),
                        )
                    )

        return self._deduplicate_matches(matches)

    def _find_first_match(
        self,
        frame: np.ndarray,
        templates: Sequence[LoadedTemplate],
    ) -> Optional[MatchResult]:
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        best_match: Optional[MatchResult] = None

        for template in templates:
            search_frame, offset = self._resolve_search_frame(gray_frame, template)
            for scaled_image, scaled_width, scaled_height in self._iter_scaled_templates(template):
                if (
                    search_frame.shape[1] < scaled_width
                    or search_frame.shape[0] < scaled_height
                ):
                    continue

                result = cv2.matchTemplate(search_frame, scaled_image, cv2.TM_CCOEFF_NORMED)
                _, max_score, _, max_location = cv2.minMaxLoc(result)
                if max_score < template.threshold:
                    continue

                candidate = MatchResult(
                    template_name=template.name,
                    score=float(max_score),
                    top_left=(
                        int(max_location[0] + offset[0]),
                        int(max_location[1] + offset[1]),
                    ),
                    click_point=(
                        int(max_location[0] + offset[0] + scaled_width // 2),
                        int(max_location[1] + offset[1] + scaled_height // 2),
                    ),
                )
                if best_match is None or candidate.score > best_match.score:
                    best_match = candidate

        return best_match

    def _resolve_search_frame(
        self,
        gray_frame: np.ndarray,
        template: LoadedTemplate,
    ) -> Tuple[np.ndarray, Tuple[int, int]]:
        if template.search_region_ratio is None:
            return gray_frame, (0, 0)

        frame_height, frame_width = gray_frame.shape[:2]
        left_ratio, top_ratio, right_ratio, bottom_ratio = template.search_region_ratio

        left = max(0, min(frame_width - 1, round(left_ratio * frame_width)))
        top = max(0, min(frame_height - 1, round(top_ratio * frame_height)))
        right = max(left + 1, min(frame_width, round(right_ratio * frame_width)))
        bottom = max(top + 1, min(frame_height, round(bottom_ratio * frame_height)))

        return gray_frame[top:bottom, left:right], (left, top)

    def _iter_scaled_templates(self, template: LoadedTemplate):
        yielded_original = False
        for scale in self.template_scales:
            if abs(scale - 1.0) < 1e-6:
                yielded_original = True
                yield template.image, template.width, template.height
                continue

            scaled_width = max(1, round(template.width * scale))
            scaled_height = max(1, round(template.height * scale))
            scaled_image = cv2.resize(
                template.image,
                (scaled_width, scaled_height),
                interpolation=cv2.INTER_LINEAR,
            )
            yield scaled_image, scaled_width, scaled_height

        if not yielded_original:
            yield template.image, template.width, template.height

    def _deduplicate_matches(self, matches: Sequence[MatchResult]) -> List[MatchResult]:
        kept_matches: List[MatchResult] = []

        for match in sorted(matches, key=lambda item: item.score, reverse=True):
            is_duplicate = False
            for kept in kept_matches:
                dx = kept.click_point[0] - match.click_point[0]
                dy = kept.click_point[1] - match.click_point[1]
                distance = (dx * dx + dy * dy) ** 0.5
                if distance < self.match_distance and kept.template_name == match.template_name:
                    is_duplicate = True
                    break

            if not is_duplicate:
                kept_matches.append(match)

        return kept_matches
