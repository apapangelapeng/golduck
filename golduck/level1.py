"""Level 1 definition."""

from __future__ import annotations

from .level import Level
from .rle import encode_rle, pattern_from_cells


BLOCK = frozenset({(0, 0), (0, 1), (1, 0), (1, 1)})


class Level1(Level):
    BIT_COUNT = 64
    BIT_SPACING = 4

    SECRET_ROWS = 4
    SECRET_COLS = BIT_COUNT // SECRET_ROWS
    SECRET_WIDTH = (SECRET_COLS - 1) * BIT_SPACING + 2
    SECRET_HEIGHT = (SECRET_ROWS - 1) * BIT_SPACING + 2

    CANVAS_X = -5000
    CANVAS_Y = -5000
    CANVAS_W = 10000
    CANVAS_H = 10000

    SECRET_X = -(SECRET_WIDTH // 2)
    SECRET_Y = -(SECRET_HEIGHT // 2)

    CONTESTANT_W = 1000
    CONTESTANT_H = 200
    CONTESTANT_X = -(CONTESTANT_W // 2)
    CONTESTANT_Y = -400 - (CONTESTANT_H // 2)

    VIEW_W = 1000
    VIEW_H = 200
    VIEW_X = -(VIEW_W // 2)
    VIEW_Y = 400 - VIEW_H // 2

    MIN_GENERATIONS = 0
    MAX_GENERATIONS = 10000
    MAX_RUNS = 16

    def __init__(self, secret: int):
        super().__init__(secret)
        self._secret_rle: str | None = None

    def get_secret(self) -> tuple[int, int, str]:
        if self._secret_rle is None:
            cells: set[tuple[int, int]] = set()
            for bit in range(self.BIT_COUNT):
                if (self.secret >> bit) & 1:
                    grid_y, grid_x = divmod(bit, self.SECRET_COLS)
                    offset_x, offset_y = grid_x * self.BIT_SPACING, grid_y * self.BIT_SPACING
                    for x, y in BLOCK:
                        cells.add((offset_x + x, offset_y + y))
            pattern = pattern_from_cells(cells, self.SECRET_WIDTH, self.SECRET_HEIGHT)
            self._secret_rle = encode_rle(pattern)
        return self.SECRET_X, self.SECRET_Y, self._secret_rle

    def get_canvas_rect(self) -> tuple[int, int, int, int]:
        return self.CANVAS_X, self.CANVAS_Y, self.CANVAS_W, self.CANVAS_H

    def get_contestant_rect(self) -> tuple[int, int, int, int]:
        return (
            self.CONTESTANT_X,
            self.CONTESTANT_Y,
            self.CONTESTANT_W,
            self.CONTESTANT_H,
        )

    def get_viewing_rect(self) -> tuple[int, int, int, int]:
        return self.VIEW_X, self.VIEW_Y, self.VIEW_W, self.VIEW_H

    def get_generation_range(self) -> tuple[int, int]:
        return self.MIN_GENERATIONS, self.MAX_GENERATIONS

    def get_max_runs(self) -> int:
        return self.MAX_RUNS
