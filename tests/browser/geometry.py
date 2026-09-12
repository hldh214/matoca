from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol


class GeometryLocator(Protocol):
    def bounding_box(
        self,
        *,
        timeout: float | timedelta | None = None,
    ) -> Any: ...

    def is_visible(self) -> bool: ...


class ViewportPage(Protocol):
    @property
    def viewport_size(self) -> Any: ...


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def has_area(self) -> bool:
        return self.width > 0 and self.height > 0

    def overlaps(self, other: Rect) -> bool:
        return (
            self.has_area
            and other.has_area
            and self.x < other.right
            and other.x < self.right
            and self.y < other.bottom
            and other.y < self.bottom
        )


def box(locator: GeometryLocator) -> Rect:
    bounding_box = locator.bounding_box()
    assert bounding_box is not None, "visible element has no bounding box"
    return Rect(
        x=bounding_box["x"],
        y=bounding_box["y"],
        width=bounding_box["width"],
        height=bounding_box["height"],
    )


def assert_no_overlap(a: Rect, b: Rect) -> None:
    assert not a.overlaps(b), f"rectangles overlap: {a!r} and {b!r}"


def assert_inside_viewport(locator: GeometryLocator, page: ViewportPage) -> None:
    assert locator.is_visible(), "element is not visible"
    element = box(locator)
    assert element.has_area, f"visible element must have positive area: {element!r}"
    viewport = page.viewport_size
    assert viewport is not None, "page must use a fixed viewport"
    assert element.x >= 0, f"element left edge is outside viewport: {element!r}"
    assert element.right <= viewport["width"], (
        f"element right edge is outside viewport: {element!r}; viewport width={viewport['width']}"
    )
