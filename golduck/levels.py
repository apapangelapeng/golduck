"""Level registry."""

import os
import re
import importlib

from .level import Level

LEVELS: dict[int, type[Level]] = {}

for fn in os.listdir(os.path.dirname(__file__)):
    m = re.match(r"^level(\d+)\.py$", fn)
    if m:
        level_nr = int(m[1])
        mod = importlib.import_module(f".level{level_nr}", __package__)
        LEVELS[level_nr] = getattr(mod, f"Level{level_nr}")
