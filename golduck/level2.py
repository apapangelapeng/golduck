"""Level 2 definition."""

from __future__ import annotations

from .level import Level
from .rle import encode_rle, pattern_from_cells
import random


class Level2(Level):
    BIT_WIDTH = 3
    BIT_HEIGHT = 2
    BIT_COUNT = 64
    SECRET_WIDTH = BIT_COUNT * BIT_WIDTH + 1
    SECRET_HEIGHT = BIT_HEIGHT

    CANVAS_X = -5000
    CANVAS_Y = -5000
    CANVAS_W = 10000
    CANVAS_H = 10000

    SECRET_X = -(SECRET_WIDTH // 2)
    SECRET_Y = -400 - (SECRET_HEIGHT // 2)

    CONTESTANT_W = 1000
    CONTESTANT_H = 200
    CONTESTANT_X = -(CONTESTANT_W // 2)
    CONTESTANT_Y = -(CONTESTANT_H // 2)

    VIEW_W = 1000
    VIEW_H = 200
    VIEW_X = -(VIEW_W // 2)
    VIEW_Y = 400 - (VIEW_H // 2)

    MIN_GENERATIONS = 0
    MAX_GENERATIONS = 10000
    MAX_RUNS = 16

    def __init__(self, secret: int):
        super().__init__(secret)
        self._secret_rle: str | None = None
        self._parity_gen = random.Random(secret)

    def get_secret(self) -> tuple[int, int, str]:
        if self._secret_rle is None:
            cells: set[tuple[int, int]] = set()
            prev_bit = False
            prev_bit_parity = None
            for bit in range(self.BIT_COUNT):
                if (self.secret >> bit) & 1:
                    x = bit * self.BIT_WIDTH
                    cells.update({(x, 0), (x, 1), (x + 3, 0), (x + 3, 1)})
                    if not prev_bit:
                        prev_bit = True
                        prev_bit_parity = self._parity_gen.randrange(2)
                    cells.update({(x + 1, prev_bit_parity), (x + 2, 1 - prev_bit_parity)})
                else:
                    prev_bit = False
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
