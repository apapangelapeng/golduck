"""Base level contract."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Level(ABC):
    def __init__(self, secret: int):
        if secret < 0 or secret >= 1 << 64:
            raise ValueError("secret must be a 64-bit unsigned integer")
        self.secret = secret

    @abstractmethod
    def get_secret(self) -> tuple[int, int, str]:
        """Return absolute x, absolute y, and RLE for the encoded secret."""

    @abstractmethod
    def get_canvas_rect(self) -> tuple[int, int, int, int]:
        """Return absolute x, absolute y, width, and height for the canvas."""

    @abstractmethod
    def get_contestant_rect(self) -> tuple[int, int, int, int]:
        """Return absolute x, absolute y, width, and height for contestant input."""

    @abstractmethod
    def get_viewing_rect(self) -> tuple[int, int, int, int]:
        """Return absolute x, absolute y, width, and height for returned output."""

    @abstractmethod
    def get_generation_range(self) -> tuple[int, int]:
        """Return inclusive minimum and maximum generation counts."""

    @abstractmethod
    def get_max_runs(self) -> int:
        """Return maximum number of accepted runs."""
