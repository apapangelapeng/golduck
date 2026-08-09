"""Sparse RLE utilities for Conway's Game of Life.

The parser stores rows as live-cell intervals. This keeps large blank areas
compact while still supporting merging and rectangular extraction without
materializing every cell in the canvas.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import merge
import re
from typing import Iterable

from sortedcontainers import SortedDict, SortedList

from .errors import InvalidPatternError


HEADER_RE = re.compile(
    r"^\s*x\s*=\s*(?P<width>\d+)\s*,\s*y\s*=\s*(?P<height>\d+)"
    r"(?:\s*,\s*rule\s*=\s*(?P<rule>[^\s]+))?\s*$",
    re.IGNORECASE,
)


Interval = tuple[int, int]
IntervalRow = SortedList[Interval]


@dataclass(frozen=True)
class RLEPattern:
    width: int
    height: int
    rows: SortedDict[int, IntervalRow]
    rule: str = "B3/S23"

    def has_cell(self, x: int, y: int) -> bool:
        intervals = self.rows.get(y)
        if not intervals:
            return False

        # (x + 1,) sorts after every interval whose start is at most x and
        # before every interval whose start is greater than x.
        index = intervals.bisect_left((x + 1,)) - 1
        return index >= 0 and x < intervals[index][1]

def parse_rle(text: str, *, require_header: bool = False) -> RLEPattern:
    """Parse a Game of Life RLE string into sparse live-cell intervals."""

    header_width = None
    header_height = None
    rule = "B3/S23"
    body_parts: list[str] = []
    header_seen = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not header_seen and not body_parts:
            match = HEADER_RE.match(line)
            if match:
                header_seen = True
                try:
                    header_width = int(match.group("width"))
                    header_height = int(match.group("height"))
                except ValueError:
                    raise InvalidPatternError("RLE dimensions are too large") from None
                if match.group("rule"):
                    rule = match.group("rule")
                continue
            if require_header:
                raise InvalidPatternError("RLE header is required")
        body_parts.append(line)

    if require_header and not header_seen:
        raise InvalidPatternError("RLE header is required")

    body = "".join(body_parts)
    rows: dict[int, list[Interval]] = {}
    x = 0
    y = 0
    count_digits: list[str] = []
    max_x = 0
    max_y = 0
    ended = False

    def take_count() -> int:
        if not count_digits:
            return 1
        try:
            value = int("".join(count_digits))
        except ValueError:
            raise InvalidPatternError("RLE repeat count is too large") from None
        count_digits.clear()
        if value <= 0:
            raise InvalidPatternError("RLE repeat counts must be positive")
        return value

    def add_live_interval(row: int, start: int, end: int) -> None:
        if start == end:
            return
        if header_height is not None and row >= header_height:
            raise InvalidPatternError("RLE live cell extends beyond declared height")
        if header_width is not None and end > header_width:
            raise InvalidPatternError("RLE live cell extends beyond declared width")
        intervals = rows.setdefault(row, [])
        if intervals and start <= intervals[-1][1]:
            last_start, last_end = intervals[-1]
            intervals[-1] = (last_start, max(last_end, end))
        else:
            intervals.append((start, end))

    for ch in body:
        if ended:
            if ch.isspace():
                continue
            raise InvalidPatternError("RLE data appears after end marker")
        if ch.isdigit():
            count_digits.append(ch)
            continue
        if ch.isspace():
            continue

        if ch == "b":
            repeat = take_count()
            x += repeat
            max_x = max(max_x, x)
            max_y = max(max_y, y + 1)
        elif ch == "o":
            repeat = take_count()
            start = x
            x += repeat
            add_live_interval(y, start, x)
            max_x = max(max_x, x)
            max_y = max(max_y, y + 1)
        elif ch == "$":
            repeat = take_count()
            max_y = max(max_y, y + repeat)
            y += repeat
            x = 0
        elif ch == "!":
            if count_digits:
                raise InvalidPatternError("RLE end marker cannot have a repeat count")
            ended = True
        else:
            raise InvalidPatternError(f"invalid RLE character {ch!r}")

        if header_height is not None and y > header_height:
            raise InvalidPatternError("RLE cursor extends beyond declared height")
        if header_width is not None and x > header_width:
            raise InvalidPatternError("RLE cursor extends beyond declared width")

    if count_digits:
        raise InvalidPatternError("RLE ends with a dangling repeat count")
    if header_height is not None and max_y > header_height:
        raise InvalidPatternError("RLE cursor extends beyond declared height")

    width = header_width if header_width is not None else max_x
    height = header_height if header_height is not None else max_y
    return RLEPattern(
        width=width,
        height=height,
        rows=SortedDict(
            (row, SortedList(intervals)) for row, intervals in rows.items()
        ),
        rule=rule,
    )


def pattern_from_cells(
    cells: Iterable[tuple[int, int]], width: int, height: int, *, rule: str = "B3/S23"
) -> RLEPattern:
    if width < 0 or height < 0:
        raise InvalidPatternError("RLE dimensions must be non-negative")

    rows: dict[int, list[Interval]] = {}
    for x, y in sorted(set(cells), key=lambda cell: (cell[1], cell[0])):
        if x < 0 or y < 0 or x >= width or y >= height:
            raise InvalidPatternError("cell lies outside requested pattern dimensions")
        intervals = rows.setdefault(y, [])
        if intervals and intervals[-1][1] == x:
            start, _ = intervals[-1]
            intervals[-1] = (start, x + 1)
        else:
            intervals.append((x, x + 1))
    return RLEPattern(
        width=width,
        height=height,
        rows=SortedDict(
            (row, SortedList(intervals)) for row, intervals in rows.items()
        ),
        rule=rule,
    )


def encode_rle(pattern: RLEPattern, *, include_header: bool = True) -> str:
    """Encode a sparse pattern as RLE."""

    parts: list[str] = []

    def token(count: int, ch: str) -> str:
        return ch if count == 1 else f"{count}{ch}"

    current_y = 0
    for row_y, intervals in pattern.rows.items():
        if row_y < current_y:
            raise InvalidPatternError("RLE rows must be ordered")
        if row_y >= pattern.height:
            raise InvalidPatternError("row lies outside pattern height")
        gap = row_y - current_y
        if gap:
            parts.append(token(gap, "$"))
            current_y = row_y

        current_x = 0
        for start, end in intervals:
            if start < current_x or end > pattern.width or start >= end:
                raise InvalidPatternError("invalid live-cell interval")
            if start > current_x:
                parts.append(token(start - current_x, "b"))
            parts.append(token(end - start, "o"))
            current_x = end

    body = "".join(parts) + "!"
    if include_header:
        return f"x = {pattern.width}, y = {pattern.height}, rule = {pattern.rule}\n{body}\n"
    return f"{body}\n"


def merge_placements(
    placements: Iterable[tuple[int, int, str | RLEPattern]],
    output_rect: tuple[int, int, int, int],
) -> RLEPattern:
    """Merge non-overlapping placed patterns into one sparse output rectangle."""

    out_x, out_y, out_w, out_h = output_rect
    if out_w < 0 or out_h < 0:
        raise InvalidPatternError("output rectangle dimensions must be non-negative")

    parsed = [
        (
            place_x,
            place_y,
            parse_rle(pattern_input)
            if isinstance(pattern_input, str)
            else pattern_input,
        )
        for place_x, place_y, pattern_input in placements
    ]
    row_sources: dict[int, list[tuple[int, IntervalRow]]] = {}
    for place_x, place_y, pattern in parsed:
        if (
            place_x < out_x
            or place_y < out_y
            or place_x + pattern.width > out_x + out_w
            or place_y + pattern.height > out_y + out_h
        ):
            raise InvalidPatternError("placed RLE pattern exceeds output rectangle")

        rel_x = place_x - out_x
        rel_y = place_y - out_y
        for row, intervals in pattern.rows.items():
            dest_row = rel_y + row
            row_sources.setdefault(dest_row, []).append((rel_x, intervals))

    merged: SortedDict[int, IntervalRow] = SortedDict()
    for row, sources in row_sources.items():
        if len(sources) == 1:
            offset, intervals = sources[0]
            merged[row] = SortedList(
                (offset + start, offset + end) for start, end in intervals
            )
        else:
            merged[row] = _merge_interval_sources(sources)

    return RLEPattern(
        width=out_w,
        height=out_h,
        rows=merged,
    )


def extract_rect(
    source: str | RLEPattern,
    source_rect: tuple[int, int, int, int],
    target_rect: tuple[int, int, int, int],
) -> RLEPattern:
    """Extract target_rect from source without expanding blank cells."""

    pattern = parse_rle(source, require_header=True) if isinstance(source, str) else source
    src_x, src_y, src_w, src_h = source_rect
    target_x, target_y, target_w, target_h = target_rect
    if src_w < 0 or src_h < 0 or target_w < 0 or target_h < 0:
        raise InvalidPatternError("rectangle dimensions must be non-negative")
    if pattern.width != src_w or pattern.height != src_h:
        raise InvalidPatternError("source RLE dimensions do not match source rectangle")
    if (
        target_x < src_x
        or target_y < src_y
        or target_x + target_w > src_x + src_w
        or target_y + target_h > src_y + src_h
    ):
        raise InvalidPatternError("target rectangle lies outside source rectangle")

    rel_target_x = target_x - src_x
    rel_target_y = target_y - src_y
    extracted: SortedDict[int, IntervalRow] = SortedDict()
    source_rows = pattern.rows.irange(
        minimum=rel_target_y,
        maximum=rel_target_y + target_h,
        inclusive=(True, False),
    )
    for source_row in source_rows:
        out_row = source_row - rel_target_y
        intervals: list[Interval] = []
        source_intervals = pattern.rows[source_row]
        first = max(0, source_intervals.bisect_left((rel_target_x,)) - 1)
        for start, end in source_intervals.islice(first):
            if start >= rel_target_x + target_w:
                break
            clipped_start = max(start, rel_target_x)
            clipped_end = min(end, rel_target_x + target_w)
            if clipped_start < clipped_end:
                intervals.append(
                    (clipped_start - rel_target_x, clipped_end - rel_target_x)
                )
        if intervals:
            extracted[out_row] = SortedList(intervals)
    return RLEPattern(
        width=target_w,
        height=target_h,
        rows=extracted,
        rule=pattern.rule,
    )


def _merge_interval_sources(
    sources: Iterable[tuple[int, IntervalRow]],
) -> IntervalRow:
    """Merge sorted, non-overlapping interval sequences in one pass."""

    def adjusted(offset: int, intervals: IntervalRow) -> Iterable[Interval]:
        for start, end in intervals:
            yield offset + start, offset + end

    adjusted_sources = (
        adjusted(offset, intervals) for offset, intervals in sources
    )
    merged_intervals: list[Interval] = []
    for start, end in merge(*adjusted_sources):
        if not merged_intervals:
            merged_intervals.append((start, end))
            continue

        old_start, old_end = merged_intervals[-1]
        if start < old_end:
            raise InvalidPatternError("placed RLE patterns overlap")
        if start == old_end:
            merged_intervals[-1] = (old_start, end)
        else:
            merged_intervals.append((start, end))
    return SortedList(merged_intervals)
