#!/usr/bin/env python3
"""Fetch, crop, and theme a public GitHub contribution calendar."""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


PALETTE = {
    "#eeeeee": "#eef2f7",
    "#c6e48b": "#dcecff",
    "#7bc96f": "#9fc5f1",
    "#239a3b": "#5b96da",
    "#196127": "#1468b7",
    "#767676": "#6c757d",
}

DISPLAY_MONTHS = 8
GRID_LEFT = 27
CELL_WIDTH = 10
RIGHT_PADDING = 2

RECT_PATTERN = re.compile(
    r'<rect\b(?P<attrs>[^>]*\bdata-date="(?P<date>\d{4}-\d{2}-\d{2})"[^>]*)/>'
)
TEXT_PATTERN = re.compile(r'<text(?P<attrs>[^>]*)>(?P<label>[^<]+)</text>')


def first_visible_month(today: date) -> date:
    month_index = today.year * 12 + today.month - 1 - (DISPLAY_MONTHS - 1)
    return date(month_index // 12, month_index % 12 + 1, 1)


def attribute_value(attrs: str, name: str) -> str:
    match = re.search(fr'\b{name}="([^"]+)"', attrs)
    if not match:
        raise ValueError(f"Missing {name!r} attribute in SVG element")
    return match.group(1)


def replace_attribute(attrs: str, name: str, value: int) -> str:
    return re.sub(fr'(\b{name}=")[^"]*(")', fr'\g<1>{value}\g<2>', attrs, count=1)


def crop_chart(chart: str, today: date) -> str:
    period_start = first_visible_month(today)
    weeks: dict[int, date] = {}

    for match in RECT_PATTERN.finditer(chart):
        x = int(attribute_value(match.group("attrs"), "x"))
        contribution_date = date.fromisoformat(match.group("date"))
        weeks[x] = max(weeks.get(x, contribution_date), contribution_date)

    visible_columns = sorted(x for x, week_end in weeks.items() if week_end >= period_start)
    if not visible_columns:
        raise ValueError("The GitHub contribution chart has no columns in the display range")

    first_column = visible_columns[0]
    columns = {old_x: GRID_LEFT + (old_x - first_column) for old_x in visible_columns}

    def crop_rect(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        x = int(attribute_value(attrs, "x"))
        if x not in columns:
            return ""
        return f'<rect{replace_attribute(attrs, "x", columns[x])}/>'

    def crop_month_label(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        if attribute_value(attrs, "y") != "10":
            return match.group(0)
        x = int(attribute_value(attrs, "x"))
        if x not in columns:
            return ""
        return f'<text{replace_attribute(attrs, "x", columns[x])}>{match.group("label")}</text>'

    chart = RECT_PATTERN.sub(crop_rect, chart)
    chart = TEXT_PATTERN.sub(crop_month_label, chart)
    chart_width = max(columns.values()) + CELL_WIDTH + RIGHT_PADDING
    return re.sub(r'\bwidth="\d+"', f'width="{chart_width}"', chart, count=1)


def fetch_chart(request: Request) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except Exception as error:  # noqa: BLE001 - retry transient network failures
            last_error = error
            if attempt < 2:
                time.sleep(2)
    raise RuntimeError(f"Could not fetch GitHub contribution chart: {last_error}")


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: update_github_contributions.py USERNAME OUTPUT METADATA_OUTPUT",
            file=sys.stderr,
        )
        return 2

    username, output_name, metadata_name = sys.argv[1:]
    request = Request(
        f"https://ghchart.rshah.org/{username}",
        headers={"User-Agent": "yinuochen-homepage contribution updater"},
    )
    chart = fetch_chart(request)

    for source, target in PALETTE.items():
        chart = chart.replace(source, target)
    chart = chart.replace("<rect ", '<rect rx="2" ry="2" ')
    chart = crop_chart(chart, datetime.now(timezone.utc).date())

    output = Path(output_name)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(chart, encoding="utf-8")
    metadata = Path(metadata_name)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(
        json.dumps(
            {"fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote themed contribution chart to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
