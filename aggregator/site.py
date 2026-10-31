"""Build the static site: index.html, jobs.json, and the RSS feed."""
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from feedgen.feed import FeedGenerator

from . import config, db, trends
from .text import US_STATES, parse_time

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "templates"
LOCAL = ZoneInfo(config.TIMEZONE)

BOARD_URLS = {
    "HigherEdJobs": "https://www.higheredjobs.com/",
    "Chronicle": "https://jobs.chronicle.com/",
    "Inside Higher Ed": "https://careers.insidehighered.com/",
    "Archives Gig": "https://archivesgig.com/",
    "SAA Careers": "https://careers.archivists.org/",
    "Simmons Jobline": "https://slis-jobline.simmons.edu/",
    "CLIR+DLF": "https://jobs.diglib.org/",
    "Code4Lib": "https://jobs.code4lib.org/",
    "Digital Preservation Coalition": "https://www.dpconline.org/news/job-vacancies",
    "USAJOBS": "https://www.usajobs.gov/",
}


def _short_date(stamp, now) -> str:
    local = stamp.astimezone(LOCAL)
    return local.strftime("%b %-d" if local.year == now.year else "%b %-d, %Y")


def _safe_url(url: str) -> bool:
    return url.startswith(("https://", "http://"))


def _board(source: str) -> str:
    return config.BOARD_BY_SOURCE.get(source, source)


def _posted(job):
    return parse_time(job["posted_date"]) or parse_time(job["first_seen"])


def _card(job, order, now) -> str:
    posted = _posted(job)
    due = parse_time(job["deadline"])
    deadline = due.date().isoformat() if due else ""
    types = [t.strip() for t in job["job_type"].split(",") if t.strip()]
    board = _board(job["source"])
    attrs = {
        "data-order": order, "data-board": board, "data-state": job["state"],
        "data-types": "|".join(types), "data-remote": job["remote"],
        "data-posted": posted.date().isoformat() if posted else "",
        "data-deadline": deadline, "data-org": job["organization"].lower(),
    }
    attr_text = " ".join(f'{k}="{escape(str(v))}"' for k, v in attrs.items())

    meta = []
    if job["location"]:
        meta.append(f'<li>{escape(job["location"])}</li>')
    if job["salary"]:
        meta.append(f'<li class="salary">{escape(job["salary"])}</li>')
    if posted:
        meta.append(f'<li>Posted <time datetime="{posted.date().isoformat()}">{_short_date(posted, now)}</time></li>')
    if deadline:
        due = due.replace(tzinfo=LOCAL)
        soon = " soon" if (due - now).days < 7 else ""
        meta.append(f'<li class="deadline{soon}">Apply by <time datetime="{deadline}">{_short_date(due, now)}</time></li>')

    tags = []
    if posted and now - posted <= timedelta(days=config.NEW_DAYS):
        tags.append('<span class="tag new">New</span>')
    if job["remote"]:
        tags.append(f'<span class="tag">{escape(job["remote"].capitalize())}</span>')
    tags.extend(f'<span class="tag">{escape(t)}</span>' for t in types)

    also, boards_shown = [], {board}
    for listing in job["listings"]:
        other = _board(listing["source"])
        if other not in boards_shown and _safe_url(listing["url"]):
            boards_shown.add(other)
            also.append(listing)
    via = f'via {escape(board)}'
    if also:
        links = ", ".join(f'<a href="{escape(l["url"])}">{escape(_board(l["source"]))}</a>' for l in also)
        via += f" · also on {links}"

    return (
        f'<li class="job" {attr_text}>'
        f'<h2 class="job-title"><a href="{escape(job["url"])}">{escape(job["title"])}</a></h2>'
        + (f'<p class="job-org">{escape(job["organization"])}</p>' if job["organization"] else "")
        + (f'<ul class="job-meta">{"".join(meta)}</ul>' if meta else "")
        + f'<p class="job-foot">{"".join(tags)}<span class="via">{via}</span></p>'
        "</li>"
    )


def _options(counts: dict, labels=None) -> str:
    labels = labels or {}
    return "".join(
        f'<option value="{escape(value)}">{escape(labels.get(value, value))} ({n})</option>'
        for value, n in counts.items())


def render_index(jobs, now) -> str:
    boards, states = {}, {}
    for job in jobs:
        boards[_board(job["source"])] = boards.get(_board(job["source"]), 0) + 1
        key = job["state"] or "other"
        states[key] = states.get(key, 0) + 1
    state_labels = {**US_STATES, "other": "Outside US or unspecified"}
    ordered_states = dict(sorted(states.items(), key=lambda kv: (kv[0] == "other", state_labels.get(kv[0], kv[0]))))
    sources = ", ".join(f'<a href="{url}">{escape(name)}</a>' for name, url in BOARD_URLS.items() if name in boards)

    replacements = {
        "{{title}}": escape(config.SITE_TITLE),
        "{{description}}": escape(config.SITE_DESCRIPTION),
        "{{active}}": f"{len(jobs):,}",
        "{{updated}}": now.astimezone(LOCAL).strftime("%b %-d, %Y at %-I:%M %p %Z"),
        "{{version}}": now.strftime("%Y%m%d%H%M"),
        "{{repo}}": config.REPO_URL,
        "{{expiry_days}}": str(config.EXPIRY_DAYS),
        "{{board_options}}": _options(dict(sorted(boards.items()))),
        "{{state_options}}": _options(ordered_states, state_labels),
        "{{sources}}": sources,
        "{{jobs}}": "\n".join(_card(job, i, now) for i, job in enumerate(jobs)),
    }
    page = (TEMPLATES / "index.html").read_text()
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    return page


def _public(job) -> dict:
    return {
        "title": job["title"],
        "organization": job["organization"],
        "location": job["location"],
        "state": job["state"],
        "remote": job["remote"],
        "salary": job["salary"],
        "job_type": job["job_type"],
        "deadline": job["deadline"],
        "posted_date": job["posted_date"],
        "first_seen": job["first_seen"],
        "description": job["summary"],
        "url": job["url"],
        "source_feed": job["source"],
        "board": _board(job["source"]),
        "listings": [{"board": _board(l["source"]), "source_feed": l["source"], "url": l["url"]}
                     for l in job["listings"]],
    }


def render_rss(jobs, now) -> bytes:
    fg = FeedGenerator()
    fg.title(config.SITE_TITLE)
    fg.description(config.SITE_DESCRIPTION)
    fg.link(href=config.SITE_URL, rel="alternate")
    fg.link(href=config.SITE_URL + "consolidated-jobs.xml", rel="self")
    fg.language("en")
    fg.lastBuildDate(now)
    newest = sorted(jobs, key=lambda j: j["first_seen"], reverse=True)[: config.RSS_MAX_ITEMS]
    # feedgen emits entries in reverse insertion order.
    for job in reversed(newest):
        entry = fg.add_entry()
        entry.title(f'{job["title"]} — {job["organization"]}' if job["organization"] else job["title"])
        entry.link(href=job["url"])
        entry.guid(job["url"], permalink=True)
        parts = [escape(v) for v in (job["organization"], job["location"], job["salary"]) if v]
        if job["deadline"]:
            parts.append(f'Apply by {escape(job["deadline"])}')
        body = "<p>" + " · ".join(parts) + "</p>" if parts else ""
        if job["summary"]:
            body += f'<p>{escape(job["summary"])}</p>'
        body += f'<p><em>Source: {escape(_board(job["source"]))}</em></p>'
        entry.description(body)
        entry.category(term=_board(job["source"]))
        entry.published(_posted(job) or now)
    return fg.rss_str(pretty=True)


def build(conn, out_dir=config.SITE_DIR, now=None) -> int:
    now = now or datetime.now(timezone.utc)
    jobs = [job for job in db.active_jobs(conn, now) if _safe_url(job["url"])]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_index(jobs, now))
    (out / "jobs.json").write_text(json.dumps(
        {"generated_at": now.isoformat(), "total_jobs": len(jobs), "jobs": [_public(j) for j in jobs]},
        indent=1, ensure_ascii=False))
    (out / "consolidated-jobs.xml").write_bytes(render_rss(jobs, now))
    trends.build(conn, jobs, out, now)
    for name in ("style.css", "app.js", "trends.js", "robots.txt"):
        shutil.copyfile(TEMPLATES / name, out / name)
    log.info("Built site with %d jobs in %s", len(jobs), out)
    return len(jobs)
