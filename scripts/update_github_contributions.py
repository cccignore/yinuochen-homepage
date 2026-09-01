#!/usr/bin/env python3
"""Build an anonymized commit calendar from repositories visible to the owner."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


DISPLAY_MONTHS = 8
CELL_SIZE = 10
CELL_STEP = 12
GRID_LEFT = 27
GRID_TOP = 20
RIGHT_PADDING = 2
PALETTE = ["#eef2f7", "#dcecff", "#9fc5f1", "#5b96da", "#1468b7"]
TEXT_COLOR = "#6c757d"


def first_visible_month(today: date) -> date:
    month_index = today.year * 12 + today.month - 1 - (DISPLAY_MONTHS - 1)
    return date(month_index // 12, month_index % 12 + 1, 1)


def request_json(url: str, token: str) -> tuple[object, str | None]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "yinuochen-homepage activity updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload, response.headers.get("Link")
        except HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504}:
                raise
        except Exception as error:  # noqa: BLE001 - retry transient network failures
            last_error = error
        if attempt < 2:
            time.sleep(2)
    raise RuntimeError(f"GitHub API request failed: {last_error}")


def next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        pieces = part.strip().split(";")
        if len(pieces) > 1 and pieces[1].strip() == 'rel="next"':
            return pieces[0].strip()[1:-1]
    return None


def paginated(url: str, token: str):
    while url:
        payload, link_header = request_json(url, token)
        if not isinstance(payload, list):
            raise RuntimeError(f"Expected a list from GitHub API: {url}")
        yield from payload
        url = next_link(link_header)


def visible_repositories(token: str) -> list[str]:
    url = (
        "https://api.github.com/user/repos?per_page=100&sort=updated"
        "&affiliation=owner,collaborator,organization_member"
    )
    repositories: list[str] = []
    for repository in paginated(url, token):
        if not repository.get("archived"):
            repositories.append(repository["full_name"])
    return repositories


def collect_commits(
    repositories: list[str],
    token: str,
    username: str,
    aliases: set[str],
    period_start: date,
    period_end: date,
) -> Counter[date]:
    counts: Counter[date] = Counter()
    since = f"{period_start.isoformat()}T00:00:00Z"
    until = f"{period_end.isoformat()}T23:59:59Z"

    for repository in repositories:
        encoded_repository = "/".join(quote(piece, safe="") for piece in repository.split("/"))
        url = (
            f"https://api.github.com/repos/{encoded_repository}/commits"
            f"?per_page=100&since={quote(since)}&until={quote(until)}"
        )
        try:
            commits = paginated(url, token)
            for commit in commits:
                linked_login = ((commit.get("author") or {}).get("login") or "").casefold()
                commit_author = commit.get("commit", {}).get("author") or {}
                author_name = (commit_author.get("name") or "").strip().casefold()
                if linked_login != username.casefold() and author_name not in aliases:
                    continue
                author_date = commit_author.get("date")
                if author_date:
                    counts[date.fromisoformat(author_date[:10])] += 1
        except HTTPError as error:
            if error.code in {409, 451}:
                continue
            raise
    return counts


def activity_level(count: int, positive_counts: list[int]) -> int:
    if count <= 0:
        return 0
    maximum = max(positive_counts, default=1)
    if count >= maximum:
        return 4
    if count >= max(4, maximum * 0.5):
        return 3
    if count >= 3:
        return 2
    return 1


def render_svg(counts: Counter[date], period_start: date, period_end: date) -> str:
    calendar_start = period_start - timedelta(days=(period_start.weekday() + 1) % 7)
    calendar_end = period_end + timedelta(days=(5 - period_end.weekday()) % 7)
    days = (calendar_end - calendar_start).days + 1
    week_count = (days + 6) // 7
    width = GRID_LEFT + week_count * CELL_STEP - (CELL_STEP - CELL_SIZE) + RIGHT_PADDING
    positive_counts = [value for value in counts.values() if value > 0]

    elements = [
        '<?xml version="1.0" standalone="no"?>',
        (
            '<svg version="1.1" xmlns="http://www.w3.org/2000/svg" '
            f'width="{width}" height="104" viewBox="0 0 {width} 104">'
        ),
    ]

    current = calendar_start
    while current <= calendar_end:
        week = (current - calendar_start).days // 7
        weekday = (current.weekday() + 1) % 7
        x = GRID_LEFT + week * CELL_STEP
        y = GRID_TOP + weekday * CELL_STEP
        count = counts.get(current, 0) if period_start <= current <= period_end else 0
        level = activity_level(count, positive_counts)
        elements.append(
            f'<rect rx="2" ry="2" style="fill:{PALETTE[level]};shape-rendering:crispedges;" '
            f'data-count="{count}" data-date="{current.isoformat()}" '
            f'x="{x}" y="{y}" width="{CELL_SIZE}" height="{CELL_SIZE}"><title>'
            f'{escape(current.isoformat())}: {count} commit{"s" if count != 1 else ""}'
            "</title></rect>"
        )
        current += timedelta(days=1)

    for label, weekday in (("Mon", 1), ("Wed", 3), ("Fri", 5)):
        y = GRID_TOP + weekday * CELL_STEP + 8
        elements.append(
            f'<text style="fill:{TEXT_COLOR};font-family:-apple-system,BlinkMacSystemFont,'
            f'\'Segoe UI\',Helvetica,Arial,sans-serif;font-size:9px;" x="0" y="{y}">{label}</text>'
        )

    cursor = date(period_start.year, period_start.month, 1)
    while cursor <= period_end:
        week = max(0, (cursor - calendar_start).days // 7)
        x = GRID_LEFT + week * CELL_STEP
        elements.append(
            f'<text style="fill:{TEXT_COLOR};font-family:-apple-system,BlinkMacSystemFont,'
            f'\'Segoe UI\',Helvetica,Arial,sans-serif;font-size:10px;" x="{x}" y="10">'
            f"{cursor.strftime('%b')}</text>"
        )
        next_month = cursor.month % 12 + 1
        next_year = cursor.year + (1 if cursor.month == 12 else 0)
        cursor = date(next_year, next_month, 1)

    elements.append("</svg>")
    return "".join(elements)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("output", type=Path)
    parser.add_argument("metadata_output", type=Path)
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        help="Additional commit author name associated with the profile",
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN or GH_TOKEN is required", file=sys.stderr)
        return 2

    today = datetime.now(timezone.utc).date()
    period_start = first_visible_month(today)
    aliases = {alias.strip().casefold() for alias in args.alias if alias.strip()}
    aliases.add(args.username.casefold())

    repositories = visible_repositories(token)
    counts = collect_commits(repositories, token, args.username, aliases, period_start, today)
    svg = render_svg(counts, period_start, today)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8")
    args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_output.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "source": "authenticated_repository_commits",
                "total_commits": sum(counts.values()),
                "active_days": sum(value > 0 for value in counts.values()),
                "repositories_scanned": len(repositories),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {sum(counts.values())} commits across "
        f"{sum(value > 0 for value in counts.values())} active days"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
