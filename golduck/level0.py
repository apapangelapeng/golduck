"""Level 0 definition."""

from __future__ import annotations

from .level import Level
from .rle import encode_rle, pattern_from_cells


LEFT_DOWN_GLIDER = frozenset(
    {
        (1, 0),
        (0, 1),
        (0, 2),
        (1, 2),
        (2, 2),
    }
)


class Level0(Level):
    BIT_COUNT = 64
    BIT_SPACING = 10
    BIT_FIELD_WIDTH = (BIT_COUNT - 1) * BIT_SPACING + 3
    STREAM_MARGIN_X = 20
    STREAM_WIDTH = BIT_FIELD_WIDTH + 2 * STREAM_MARGIN_X
    STREAM_HEIGHT = 30

    SECRET_WINDOW_X = 400
    SECRET_WINDOW_Y = 100
    SECRET_X = SECRET_WINDOW_X + STREAM_MARGIN_X
    SECRET_Y = SECRET_WINDOW_Y

    TRAVEL_DISTANCE = 300
    SOLVE_GENERATIONS = TRAVEL_DISTANCE * 4

    CONTESTANT_X = SECRET_WINDOW_X - TRAVEL_DISTANCE // 2
    CONTESTANT_Y = SECRET_WINDOW_Y + TRAVEL_DISTANCE // 2
    VIEW_X = CONTESTANT_X
    VIEW_Y = CONTESTANT_Y

    CANVAS_X = -10000
    CANVAS_Y = -10000
    CANVAS_W = 22000
    CANVAS_H = 21000

    MIN_GENERATIONS = 1000
    MAX_GENERATIONS = 10000
    MAX_RUNS = 1

    def __init__(self, secret: int):
        super().__init__(secret)
        self._secret_rle: str | None = None

    def get_secret(self) -> tuple[int, int, str]:
        if self._secret_rle is None:
            cells: set[tuple[int, int]] = set()
            for bit in range(self.BIT_COUNT):
                if (self.secret >> bit) & 1:
                    offset_x = bit * self.BIT_SPACING
                    for x, y in LEFT_DOWN_GLIDER:
                        cells.add((offset_x + x, y))
            pattern = pattern_from_cells(cells, self.BIT_FIELD_WIDTH, 3)
            self._secret_rle = encode_rle(pattern)
        return self.SECRET_X, self.SECRET_Y, self._secret_rle

    def get_canvas_rect(self) -> tuple[int, int, int, int]:
        return self.CANVAS_X, self.CANVAS_Y, self.CANVAS_W, self.CANVAS_H

    def get_contestant_rect(self) -> tuple[int, int, int, int]:
        return (
            self.CONTESTANT_X,
            self.CONTESTANT_Y,
            self.STREAM_WIDTH,
            self.STREAM_HEIGHT,
        )

    def get_viewing_rect(self) -> tuple[int, int, int, int]:
        return self.VIEW_X, self.VIEW_Y, self.STREAM_WIDTH, self.STREAM_HEIGHT

    def get_generation_range(self) -> tuple[int, int]:
        return self.MIN_GENERATIONS, self.MAX_GENERATIONS

    def get_max_runs(self) -> int:
        return self.MAX_RUNS
