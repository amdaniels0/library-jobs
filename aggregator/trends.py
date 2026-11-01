"""The Trends page: monthly postings, where and what kind of jobs are open, listed pay, deadlines, and durations."""
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .text import US_STATES, job_family, location_bucket, parse_time

TEMPLATES = Path(__file__).parent / "templates"
LOCAL = ZoneInfo(config.TIMEZONE)
LOCATION_GROUP_LABELS = {"us": "US, state not listed", "country": "Outside the US", "none": "Location not listed"}
MIN_PAY_SAMPLE = 5  # families with fewer yearly salaries than this aren't shown in the pay chart
GAP_DAYS = 3  # days without any collector run that count as an outage

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


def _location(job):
    return location_bucket(job["location"], job["state"], job["source"] in config.US_ONLY_SOURCES)


def _monthly(conn, now):
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
    return [{"month": m, "new_jobs": firsts.get(m, 0), "note": " ".join(notes[m])} for m in months]


def _durations(conn):
    """Days between first and last sighting for closed postings whose whole life the collector saw.

    Only boards whose feeds list every open job count (config.COMPLETE_FEED_BOARDS). Jobs first seen on the
    first day of a collection stretch were already open, and jobs last seen on its final day may have stayed
    up while the collector was off, so both are left out.
    """
    rows = conn.execute("SELECT first_seen, last_seen, active, source FROM jobs").fetchall()
    run_days = sorted({date.fromisoformat(stamp[:10]) for row in rows for stamp in row[:2]})
    starts, ends = set(), set()
    for i, day in enumerate(run_days):
        if i == 0 or (day - run_days[i - 1]).days > GAP_DAYS:
            starts.add(day)
        if i + 1 == len(run_days) or (run_days[i + 1] - day).days > GAP_DAYS:
            ends.add(day)
    days = []
    for first, last, active, source in rows:
        if active or source not in config.COMPLETE_FEED_SOURCES:
            continue
        if date.fromisoformat(first[:10]) in starts or date.fromisoformat(last[:10]) in ends:
            continue
        days.append((parse_time(last) - parse_time(first)).total_seconds() / 86400)
    return days


def compute(conn, jobs, now) -> dict:
    """All numbers the page shows; also published as trends.json."""
    board = lambda source: config.BOARD_BY_SOURCE.get(source, source)  # noqa: E731

    pay = [(job, p) for job, p in ((job, pay_value(job["salary"])) for job in jobs) if p]
    yearly = sorted(v for _, (kind, v) in pay if kind == "year")
    hourly = sorted(v for _, (kind, v) in pay if kind == "hour")
    buckets = [("Under $40K", 0, 40000)] + [
        (f"${lo // 1000}–{lo // 1000 + 10}K", lo, lo + 10000) for lo in range(40000, 140000, 10000)
    ] + [("$140K and up", 140000, math.inf)]

    types = Counter()
    for job in jobs:
        for label in filter(None, (t.strip() for t in job["job_type"].split(","))):
            types[label] += 1
        if job["remote"]:
            types[job["remote"].capitalize()] += 1

    locations = Counter(_location(job) for job in jobs)
    states = Counter({value: n for (kind, value), n in locations.items() if kind == "state"})
    by_location = [{"location": US_STATES.get(code, code), "jobs": n} for code, n in states.most_common(15)]
    by_location += [{"location": label, "jobs": sum(n for (k, _), n in locations.items() if k == kind)}
                    for kind, label in LOCATION_GROUP_LABELS.items()]
    countries = Counter({value: n for (kind, value), n in locations.items() if kind == "country"})

    families = Counter(job_family(job["title"]) for job in jobs)
    family_pay = defaultdict(list)
    for job, (kind, value) in pay:
        if kind == "year":
            family_pay[job_family(job["title"])].append(value)

    today = now.astimezone(LOCAL).date()
    week_start = today - timedelta(days=today.weekday())
    deadlines = [d for d in (parse_time(job["deadline"]) for job in jobs) if d]
    deadline_weeks = [{"week_of": (week_start + timedelta(weeks=w)).isoformat(),
                       "jobs": sum(1 for d in deadlines
                                   if week_start + timedelta(weeks=w) <= d.date() < week_start + timedelta(weeks=w + 1))}
                      for w in range(8)]

    durations = _durations(conn)
    duration_buckets = [("Under 1 week", 0, 7), ("1–2 weeks", 7, 14), ("2–4 weeks", 14, 28),
                        ("4–8 weeks", 28, 56), ("8–12 weeks", 56, 84), ("12 weeks or more", 84, math.inf)]

    week_ago = now - timedelta(days=config.NEW_DAYS)
    return {
        "generated_at": now.isoformat(),
        "open_jobs": len(jobs),
        "posted_past_week": sum(1 for job in jobs if (_posted(job) or now) >= week_ago),
        "closing_next_two_weeks": sum(1 for d in deadlines if today <= d.date() < today + timedelta(days=14)),
        "listing_pay": len(pay),
        "median_yearly_pay": statistics.median(yearly) if yearly else None,
        "median_hourly_pay": statistics.median(hourly) if hourly else None,
        "yearly_pay_count": len(yearly),
        "hourly_pay_count": len(hourly),
        "new_per_month": _monthly(conn, now),
        "open_by_location": by_location,
        "open_outside_us_by_country": [{"country": c, "jobs": n} for c, n in countries.most_common()],
        "open_by_family": [{"family": f, "jobs": n} for f, n in
                           sorted(families.items(), key=lambda kv: (kv[0] == "Other", -kv[1]))],
        "median_yearly_pay_by_family": sorted(
            ({"family": f, "median": statistics.median(v), "jobs_with_pay": len(v)}
             for f, v in family_pay.items() if len(v) >= MIN_PAY_SAMPLE),
            key=lambda row: row["median"], reverse=True),
        "deadlines_by_week": deadline_weeks,
        "jobs_with_deadline": len(deadlines),
        "posting_duration_days_median": statistics.median(durations) if durations else None,
        "posting_duration_sample": len(durations),
        "posting_duration_distribution": [{"range": label, "jobs": sum(1 for d in durations if lo <= d < hi)}
                                          for label, lo, hi in duration_buckets],
        "open_by_type": [{"type": name, "jobs": n} for name, n in types.most_common()],
        "open_by_board": [{"board": name, "jobs": n}
                          for name, n in Counter(board(job["source"]) for job in jobs).most_common()],
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


def _chart(title, subtitle, body, table, anchor="") -> str:
    attr = f' id="{anchor}"' if anchor else ""
    return (f'<section class="chart"{attr}><h2>{escape(title)}</h2><p class="chart-sub">{escape(subtitle)}</p>'
            f"{body}{table}</section>")


def column_chart(points, unit) -> str:
    """points: [(label, short_label, value, note)]. One series; empty periods with a note show 'No data'."""
    top = _nice_max(max((v for _, _, v, _ in points), default=0))
    grid = "".join(f'<div class="grid-line" style="bottom:{tick / top * 100:.2f}%"><span>{tick:,.0f}</span></div>'
                   for tick in (0, top / 2, top))
    columns, labels = [], []
    for label, short, value, note in points:
        tip = f"{value:,} {unit}" if value or not note else "No data"
        aria = f"{label}: {tip}" + (f". {note}" if note else "")
        if value:
            mark = f'<span class="bar" style="height:{value / top * 100:.2f}%"></span>'
        else:  # a dash only where data is missing; a real zero is just empty
            mark = '<span class="no-data">–</span>' if note else ""
        columns.append(f'<div class="col" tabindex="0" data-value="{escape(tip)}" data-label="{escape(label)}"'
                       f' data-note="{escape(note)}" aria-label="{escape(aria)}">{mark}</div>')
        # First line: month or week; second line: the year where it changes, plus the note marker.
        first, _, second = short.partition(" ")
        extra = escape(second) + ("*" if note else "")
        labels.append(f"<span>{escape(first)}<br>{extra or '&nbsp;'}</span>")
    n = len(points)
    return (f'<div class="plot" style="--n:{n}">{grid}<div class="cols">{"".join(columns)}</div></div>'
            f'<div class="x-labels" style="--n:{n}" aria-hidden="true">{"".join(labels)}</div>')


def bar_chart(rows, unit, fmt=lambda v: f"{v:,}") -> str:
    """rows: [(label, value)] as horizontal bars, formatted value at the tip."""
    top = max((v for _, v in rows), default=0) or 1
    items = []
    for label, value in rows:
        shown = fmt(value)
        tip = f"{shown} {unit}".strip()
        bar = f'<span class="hbar" style="width:{value / top * 100:.2f}%"></span>' if value else ""
        items.append(
            f'<span class="hbar-label">{escape(label)}</span>'
            f'<span class="hbar-track" tabindex="0" data-value="{escape(tip)}" data-label="{escape(label)}"'
            f' aria-label="{escape(label)}: {escape(tip)}">{bar}'
            f'<span class="hbar-value">{escape(shown)}</span></span>')
    return f'<div class="hbars">{"".join(items)}</div>'


def _money(value, hourly=False) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}/hr" if hourly else f"${value / 1000:,.0f}K"


def render(stats, now) -> str:
    total = stats["open_jobs"] or 1
    duration = stats["posting_duration_days_median"]
    tiles = [
        ("Open jobs", f'{stats["open_jobs"]:,}', "Listed by at least one board"),
        ("Posted in the past week", f'{stats["posted_past_week"]:,}', "By posting date"),
        ("Closing in the next 2 weeks", f'{stats["closing_next_two_weeks"]:,}',
         f'Of {stats["jobs_with_deadline"]:,} jobs that list a deadline'),
        ("Listing pay", f'{stats["listing_pay"] / total:.0%}', f'{stats["listing_pay"]:,} of {stats["open_jobs"]:,} open jobs'),
        ("Median listed salary", _money(stats["median_yearly_pay"]),
         f'Yearly pay, {stats["yearly_pay_count"]:,} jobs · hourly median '
         f'{_money(stats["median_hourly_pay"], hourly=True)} ({stats["hourly_pay_count"]:,} jobs)'),
        ("Typical time listed", f"{duration / 7:.0f} weeks" if duration else "—",
         f'Median of {stats["posting_duration_sample"]:,} closed postings'),
    ]
    tile_html = "".join(f'<div class="tile"><p class="tile-label">{escape(label)}</p>'
                        f'<p class="tile-value">{escape(value)}</p><p class="tile-note">{escape(note)}</p></div>'
                        for label, value, note in tiles)

    months = stats["new_per_month"]

    def month_short(i, month):
        day = datetime.strptime(month, "%Y-%m")
        return day.strftime("%b '%y") if i == 0 or day.month == 1 else day.strftime("%b")

    points = [(_month_label(m["month"]), month_short(i, m["month"]), m["new_jobs"], m["note"])
              for i, m in enumerate(months)]
    month_notes = "".join(f'<li><strong>{escape(_month_label(m["month"]))}*</strong> {escape(m["note"])}</li>'
                          for m in months if m["note"])
    monthly = _chart(
        "New postings per month",
        f'Jobs first seen each month, {_month_label(months[0]["month"])} to {_month_label(months[-1]["month"])}.'
        if months else "No data yet.",
        column_chart(points, "new postings") + (f'<ul class="chart-notes">{month_notes}</ul>' if month_notes else ""),
        _table(["Month", "New postings", "Note"], [(p[0], f"{p[2]:,}", p[3]) for p in points]))

    weeks = stats["deadlines_by_week"]
    week_points = [(f'Week of {datetime.fromisoformat(w["week_of"]).strftime("%b %-d")}',
                    datetime.fromisoformat(w["week_of"]).strftime("%b %-d"), w["jobs"], "") for w in weeks]
    deadlines = _chart(
        "Upcoming application deadlines",
        f'Open jobs by the week their deadline falls in, next 8 weeks. Only {stats["jobs_with_deadline"]:,} of '
        f'{stats["open_jobs"]:,} open jobs list a deadline.',
        column_chart(week_points, "deadlines"),
        _table(["Week", "Deadlines"], [(p[0], f"{p[2]:,}") for p in week_points]))

    def bars(title, subtitle, rows, label_key, unit, header, value_key="jobs", fmt=lambda v: f"{v:,}", value_header="Open jobs"):
        data = [(r[label_key], r[value_key]) for r in rows]
        return _chart(title, subtitle, bar_chart(data, unit, fmt),
                      _table([header, value_header], [(name, fmt(n)) for name, n in data]))

    family_pay_rows = [{"family": f'{r["family"]} ({r["jobs_with_pay"]})', "median": r["median"]}
                       for r in stats["median_yearly_pay_by_family"]]
    countries = stats["open_outside_us_by_country"]
    sections = [
        monthly,
        bars("Where the open jobs are",
             "Top 15 states, then jobs without a state: US jobs whose posting gives no state, jobs abroad, "
             "and jobs with no location.", stats["open_by_location"], "location", "open jobs", "Location"),
        bars("Outside the US, by country", f'{sum(c["jobs"] for c in countries):,} open jobs abroad, by country.',
             countries, "country", "open jobs", "Country") if countries else "",
        bars("Job families", "Open jobs grouped by title. Titles are matched by keyword, so read this as approximate.",
             stats["open_by_family"], "family", "open jobs", "Job family"),
        bars("Median listed salary by job family",
             f"Yearly pay midpoints, for families with at least {MIN_PAY_SAMPLE} open jobs that list a salary. "
             "The number in parentheses is how many jobs each median is based on.",
             family_pay_rows, "family", "", "Job family (jobs with pay)", value_key="median",
             fmt=lambda v: _money(v), value_header="Median salary"),
        bars("Listed pay, yearly", f'Open jobs with a yearly salary or range ({stats["yearly_pay_count"]:,}), '
             "by midpoint. Most postings don't list pay, so this is not a market-wide figure.",
             stats["yearly_pay_distribution"], "range", "jobs", "Salary midpoint"),
        deadlines,
        bars("How long postings stay up",
             f'Time from first to last sighting for {stats["posting_duration_sample"]:,} closed postings on boards '
             f'whose feeds list every open job ({", ".join(sorted(config.COMPLETE_FEED_BOARDS))}), leaving out any '
             "that straddle a collection gap. Board posting packages shape this; it measures listings, not time to hire.",
             stats["posting_duration_distribution"], "range", "postings", "Time listed", value_header="Postings"),
        bars("Job types", "Open jobs by type, where the posting or title says so. A job can have more than one.",
             stats["open_by_type"], "type", "open jobs", "Type"),
        bars("Where the listings come from", "Open jobs by the board that first listed them.",
             stats["open_by_board"], "board", "open jobs", "Board"),
        bars("Employers with the most openings", "Open jobs per employer, top 12.",
             stats["top_employers"], "employer", "open jobs", "Employer"),
    ]
    replacements = {
        "{{title}}": escape(f"Trends · {config.SITE_TITLE}"),
        "{{site_title}}": escape(config.SITE_TITLE),
        "{{updated}}": now.astimezone(LOCAL).strftime("%b %-d, %Y at %-I:%M %p %Z"),
        "{{version}}": now.strftime("%Y%m%d%H%M"),
        "{{repo}}": config.REPO_URL,
        "{{tiles}}": tile_html,
        "{{charts}}": "\n".join(s for s in sections if s),
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
