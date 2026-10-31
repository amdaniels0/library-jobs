"""The Trends page: monthly postings, where and what kind of jobs are open, and listed pay."""
import json
import math
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .text import US_STATES, parse_time

TEMPLATES = Path(__file__).parent / "templates"
LOCAL = ZoneInfo(config.TIMEZONE)
OTHER_LOCATION = "Outside US or unspecified"

_AMOUNT_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?([kK])?\b")


def pay_value(salary: str):
    """'$52,000 - $60,000/year' -> ('year', 56000.0); '$20-$25/hour' -> ('hour', 22.5); unclear -> None."""
    amounts = [float(n.replace(",", "")) * (1000 if k else 1) for n, k in _AMOUNT_RE.findall(salary or "")][:2]
    if not amounts:
        return None
    value = sum(amounts) / len(amounts)
    text = salary.lower()
    if re.search(r"hour|/\s?hr\b", text):
        kind = "hour"
    elif re.search(r"year|annual|annum|/\s?yr\b", text):
        kind = "year"
    elif re.search(r"month|week|day|stipend|semester", text):
        return None
    else:
        kind = "year" if value >= 15000 else "hour" if value < 200 else None
    if kind == "year" and 15000 <= value <= 400000:
        return kind, value
    if kind == "hour" and 7 <= value <= 200:
        return kind, value
    return None


def _month_label(month: str) -> str:
    return datetime.strptime(month, "%Y-%m").strftime("%b %Y")


def _months_between(first: str, last: str) -> list:
    year, month = map(int, first.split("-"))
    months = []
    while f"{year:04d}-{month:02d}" <= last:
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _posted(job):
    return parse_time(job["posted_date"]) or parse_time(job["first_seen"])


def compute(conn, jobs, now) -> dict:
    """All numbers the page shows; also published as trends.json."""
    board = lambda source: config.BOARD_BY_SOURCE.get(source, source)  # noqa: E731
    current_month = now.astimezone(LOCAL).strftime("%Y-%m")
    firsts = Counter(row[0][:7] for row in conn.execute("SELECT first_seen FROM jobs"))
    months = _months_between(min(firsts), current_month) if firsts else []

    notes = {month: [] for month in months}
    for i, month in enumerate(months):
        if firsts.get(month, 0) == 0:
            notes[month].append("No data: the collector was offline.")
            continue
        if i == 0:
            notes[month].append("Tracking began; includes every job already open.")
        elif firsts.get(months[i - 1], 0) == 0:
            notes[month].append("Collection resumed; includes every job already open.")
        if i + 1 < len(months) and firsts.get(months[i + 1], 0) == 0:
            notes[month].append("Partial month: the collector went offline partway through.")
        if month == current_month:
            notes[month].append("Month in progress.")
    for month, note in config.TRENDS_NOTES.items():
        if month in notes:
            notes[month].append(note)
    notes = {month: " ".join(parts) for month, parts in notes.items()}

    pay = [p for p in (pay_value(job["salary"]) for job in jobs) if p]
    yearly = sorted(v for kind, v in pay if kind == "year")
    hourly = sorted(v for kind, v in pay if kind == "hour")
    buckets = [("Under $40K", 0, 40000)] + [
        (f"${lo // 1000}–{lo // 1000 + 10}K", lo, lo + 10000) for lo in range(40000, 140000, 10000)
    ] + [("$140K and up", 140000, math.inf)]

    types = Counter()
    for job in jobs:
        for label in filter(None, (t.strip() for t in job["job_type"].split(","))):
            types[label] += 1
        if job["remote"]:
            types[job["remote"].capitalize()] += 1

    states = Counter(job["state"] or "other" for job in jobs)
    top_states = [(US_STATES.get(code, OTHER_LOCATION), n) for code, n in states.most_common() if code != "other"][:15]
    if states.get("other"):
        top_states.append((OTHER_LOCATION, states["other"]))

    week_ago = now - timedelta(days=config.NEW_DAYS)
    return {
        "generated_at": now.isoformat(),
        "open_jobs": len(jobs),
        "posted_past_week": sum(1 for job in jobs if (_posted(job) or now) >= week_ago),
        "listing_pay": len(pay),
        "median_yearly_pay": statistics.median(yearly) if yearly else None,
        "median_hourly_pay": statistics.median(hourly) if hourly else None,
        "yearly_pay_count": len(yearly),
        "hourly_pay_count": len(hourly),
        "new_per_month": [{"month": m, "new_jobs": firsts.get(m, 0), "note": notes.get(m, "")} for m in months],
        "open_by_state": [{"location": name, "jobs": n} for name, n in top_states],
        "open_by_board": [{"board": name, "jobs": n}
                          for name, n in Counter(board(job["source"]) for job in jobs).most_common()],
        "open_by_type": [{"type": name, "jobs": n} for name, n in types.most_common()],
        "yearly_pay_distribution": [{"range": label, "jobs": sum(1 for v in yearly if lo <= v < hi)}
                                    for label, lo, hi in buckets],
        "top_employers": [{"employer": name, "jobs": n} for name, n in
                          Counter(job["organization"] for job in jobs if job["organization"]).most_common(12)],
    }


def _nice_max(value):
    """Round an axis maximum up to 1, 2, or 5 times a power of ten."""
    if value <= 0:
        return 1
    power = 10 ** math.floor(math.log10(value))
    return next(step * power for step in (1, 2, 5, 10) if step * power >= value)


def _table(headers, rows) -> str:
    head = "".join(f"<th scope=\"col\">{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f'<details class="table-view"><summary>Show as table</summary><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></details>'


def _chart(title, subtitle, body, table) -> str:
    return (f'<section class="chart"><h2>{escape(title)}</h2><p class="chart-sub">{escape(subtitle)}</p>'
            f"{body}{table}</section>")


def column_chart(points, unit) -> str:
    """points: [(label, short_label, value, note)]. One series; empty months show 'No data'."""
    top = _nice_max(max((v for _, _, v, _ in points), default=0))
    grid = "".join(f'<div class="grid-line" style="bottom:{tick / top * 100:.2f}%"><span>{tick:,.0f}</span></div>'
                   for tick in (0, top / 2, top))
    columns, labels = [], []
    for label, short, value, note in points:
        tip = f"{value:,} {unit}" if value else "No data"
        aria = f"{label}: {tip}" + (f". {note}" if note else "")
        mark = (f'<span class="bar" style="height:{value / top * 100:.2f}%"></span>' if value
                else '<span class="no-data">–</span>')
        columns.append(f'<div class="col" tabindex="0" data-value="{escape(tip)}" data-label="{escape(label)}"'
                       f' data-note="{escape(note)}" aria-label="{escape(aria)}">{mark}</div>')
        # Month on the first line; the year (where it changes) and the note marker on the second.
        month, _, year = short.partition(" ")
        extra = escape(year) + ("*" if note else "")
        labels.append(f"<span>{escape(month)}<br>{extra or '&nbsp;'}</span>")
    n = len(points)
    return (f'<div class="plot" style="--n:{n}">{grid}<div class="cols">{"".join(columns)}</div></div>'
            f'<div class="x-labels" style="--n:{n}" aria-hidden="true">{"".join(labels)}</div>')


def bar_chart(rows, unit) -> str:
    """rows: [(label, value)] as horizontal bars, value at the tip."""
    top = max((v for _, v in rows), default=0) or 1
    items = []
    for label, value in rows:
        tip = f"{value:,} {unit}"
        bar = f'<span class="hbar" style="width:{value / top * 100:.2f}%"></span>' if value else ""
        items.append(
            f'<span class="hbar-label">{escape(label)}</span>'
            f'<span class="hbar-track" tabindex="0" data-value="{escape(tip)}" data-label="{escape(label)}"'
            f' aria-label="{escape(label)}: {escape(tip)}">{bar}'
            f'<span class="hbar-value">{value:,}</span></span>')
    return f'<div class="hbars">{"".join(items)}</div>'


def _money(value, hourly=False) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}/hr" if hourly else f"${value / 1000:,.0f}K"


def render(stats, now) -> str:
    total = stats["open_jobs"] or 1
    tiles = [
        ("Open jobs", f'{stats["open_jobs"]:,}', "Listed by at least one board"),
        ("Posted in the past week", f'{stats["posted_past_week"]:,}', "By posting date"),
        ("Listing pay", f'{stats["listing_pay"] / total:.0%}', f'{stats["listing_pay"]:,} of {stats["open_jobs"]:,} open jobs'),
        ("Median listed salary", _money(stats["median_yearly_pay"]),
         f'Yearly pay, {stats["yearly_pay_count"]:,} jobs · hourly median '
         f'{_money(stats["median_hourly_pay"], hourly=True)} ({stats["hourly_pay_count"]:,} jobs)'),
    ]
    tile_html = "".join(f'<div class="tile"><p class="tile-label">{escape(label)}</p>'
                        f'<p class="tile-value">{escape(value)}</p><p class="tile-note">{escape(note)}</p></div>'
                        for label, value, note in tiles)

    months = stats["new_per_month"]
    def short(i, month):
        # The year appears where it changes: "Nov '25", "Dec", "Jan '26".
        date = datetime.strptime(month, "%Y-%m")
        return date.strftime("%b '%y") if i == 0 or date.month == 1 else date.strftime("%b")

    points = [(_month_label(m["month"]), short(i, m["month"]), m["new_jobs"], m["note"])
              for i, m in enumerate(months)]
    year_marks = "".join(
        f'<li><strong>{escape(_month_label(m["month"]))}*</strong> {escape(m["note"])}</li>'
        for m in months if m["note"])
    monthly = _chart(
        "New postings per month",
        f'Jobs first seen each month, {_month_label(months[0]["month"])} to {_month_label(months[-1]["month"])}.'
        if months else "No data yet.",
        column_chart(points, "new postings") + (f'<ul class="chart-notes">{year_marks}</ul>' if year_marks else ""),
        _table(["Month", "New postings", "Note"], [(p[0], f"{p[2]:,}", p[3]) for p in points]))

    def bars(title, subtitle, rows, key, label_key, unit, header):
        data = [(r[label_key], r[key]) for r in rows]
        return _chart(title, subtitle, bar_chart(data, unit),
                      _table([header, "Open jobs"], [(name, f"{n:,}") for name, n in data]))

    sections = [
        monthly,
        bars("Where the open jobs are", "Top 15 states, plus jobs outside the US or without a location.",
             stats["open_by_state"], "jobs", "location", "open jobs", "Location"),
        bars("Listed pay, yearly", f'Open jobs with a yearly salary or range ({stats["yearly_pay_count"]:,}), '
             "by midpoint. Most postings don't list pay, so this is not a market-wide figure.",
             stats["yearly_pay_distribution"], "jobs", "range", "jobs", "Salary midpoint"),
        bars("Job types", "Open jobs by type, where the posting or title says so. A job can have more than one.",
             stats["open_by_type"], "jobs", "type", "open jobs", "Type"),
        bars("Where the listings come from", "Open jobs by the board that first listed them.",
             stats["open_by_board"], "jobs", "board", "open jobs", "Board"),
        bars("Employers with the most openings", "Open jobs per employer, top 12.",
             stats["top_employers"], "jobs", "employer", "open jobs", "Employer"),
    ]
    replacements = {
        "{{title}}": escape(f"Trends · {config.SITE_TITLE}"),
        "{{site_title}}": escape(config.SITE_TITLE),
        "{{updated}}": now.astimezone(LOCAL).strftime("%b %-d, %Y at %-I:%M %p %Z"),
        "{{version}}": now.strftime("%Y%m%d%H%M"),
        "{{repo}}": config.REPO_URL,
        "{{tiles}}": tile_html,
        "{{charts}}": "\n".join(sections),
    }
    page = (TEMPLATES / "trends.html").read_text()
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    return page


def build(conn, jobs, out, now):
    stats = compute(conn, jobs, now)
    (out / "trends.html").write_text(render(stats, now))
    (out / "trends.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False))
    return stats
