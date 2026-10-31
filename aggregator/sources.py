"""Fetch RSS feeds and turn entries into job dicts, with one parser per board format."""
import logging
import os
import re
import time
from datetime import datetime, timezone

import feedparser
import requests

from . import config
from .text import (canonical_url, clean, clean_lines, extract_salary, format_pay, job_types, parse_location,
                   parse_us_date, relevant_title, remote_status, state_code, truncate)

log = logging.getLogger(__name__)


def _posted(entry) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return ""
    return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()


def _job(entry, source, *, title, organization="", location="", salary="", summary="", deadline="",
         employment=(), remote="", url=None, posted=None):
    display_location, state = parse_location(location)
    return {
        "title": title,
        "organization": organization,
        "location": display_location,
        "state": state,
        "remote": remote or remote_status(title, location),
        "salary": salary,
        "job_type": ", ".join(job_types(title, employment)),
        "deadline": deadline,
        "summary": truncate(summary),
        "url": canonical_url(url or entry.get("link", "")),
        "source": source["name"],
        "posted_date": posted if posted is not None else _posted(entry),
    }


def _body(entry) -> str:
    content = entry.get("content") or [{}]
    return clean(content[0].get("value") or entry.get("summary", ""))


def _tags(entry) -> list:
    return [clean(tag.get("term")) for tag in entry.get("tags", []) if tag.get("term")]


def parse_higheredjobs(entry, source):
    """Title is the position; summary is 'Organization (City, ST)'."""
    summary = clean(entry.get("summary", ""))
    organization, location = summary, ""
    match = re.match(r"^(.*)\(([^()]*)\)\s*$", summary)
    if match:
        organization, location = match.group(1).strip(), match.group(2).strip()
    return _job(entry, source, title=clean(entry.get("title")), organization=organization,
                location=location, summary="")


def parse_madgex(entry, source):
    """Chronicle and Inside Higher Ed. Title is 'Organization: Position'.

    Summary lines: optional 'Salary …:' line, 'Organization:' line, description, location last.
    """
    title = clean(entry.get("title"))
    organization = ""
    if ": " in title:
        organization, title = (part.strip() for part in title.split(": ", 1))
    lines = clean_lines(entry.get("summary", ""))
    salary = ""
    if lines and (lines[0].lower().startswith("salary") or "$" in lines[0]):
        salary = extract_salary(lines.pop(0))
    if lines and lines[0].rstrip(":").strip() == organization:
        lines.pop(0)
    location = lines.pop() if lines and len(lines[-1]) <= 80 else ""
    if lines and lines[0] == title:
        lines.pop(0)
    return _job(entry, source, title=title, organization=organization, location=location,
                salary=salary, summary=" ".join(lines))


_GIG_LABELS = [
    "Job Opening Title", "Name of Employer", "Job Site Location (City AND State)",
    "Application Deadline", "Minimum Degree Requirement", "Certification Requirement",
    "Years of experience required", "Required Skills", "Salary Range", "Salary",
    "Job Opening URL", "Job Description (if URL to posting not available)",
    "Include instructions for how to apply",
]
_GIG_LABEL_RE = re.compile("(" + "|".join(re.escape(label) for label in _GIG_LABELS) + r")\s*:", re.IGNORECASE)


def _gig_fields(text: str) -> dict:
    """Archives Gig posts are 'Label: value' pairs, often run together on one line."""
    fields = {}
    matches = list(_GIG_LABEL_RE.finditer(text))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        label = match.group(1).lower()
        fields.setdefault(label, text[match.end():end].strip(" :;"))
    return fields


def parse_archivesgig(entry, source):
    """Title is 'City, ST: Position, Organization'; body has labelled fields."""
    raw_title = clean(entry.get("title"))
    location, rest = "", raw_title
    if ": " in raw_title:
        location, rest = (part.strip() for part in raw_title.split(": ", 1))
    fields = _gig_fields(_body(entry))
    organization = fields.get("name of employer", "")
    title = rest
    if organization and rest.lower().endswith(", " + organization.lower()):
        title = rest[: -len(organization) - 2]
    elif not organization and ", " in rest:
        title, organization = (part.strip() for part in rest.rsplit(", ", 1))
    salary = extract_salary(fields.get("salary range", "") or fields.get("salary", ""))
    deadline = parse_us_date(fields.get("application deadline", ""))
    description = fields.get("job description (if url to posting not available)", "")
    return _job(entry, source, title=title.strip(), organization=organization.strip(),
                location=location or fields.get("job site location (city and state)", ""),
                salary=salary, deadline=deadline, summary=description)


def parse_saa(entry, source):
    """SAA Careers: title is 'Position | Organization'; summary starts 'City, State, …'."""
    title = clean(entry.get("title"))
    organization = ""
    if " | " in title:
        title, organization = (part.strip() for part in title.rsplit(" | ", 1))
    summary = clean(entry.get("summary", ""))
    location = ""
    match = re.match(r"^\s*([^,]{2,40}),\s*([A-Za-z .]{2,25}),\s*", summary)
    if match and parse_location(f"{match.group(1)}, {match.group(2)}")[1]:
        location = f"{match.group(1)}, {match.group(2)}"
        summary = summary[match.end():]
    return _job(entry, source, title=title, organization=organization, location=location,
                salary=extract_salary(summary), summary=summary)


def parse_simmons(entry, source):
    """Simmons SLIS Jobline: title is 'Position, Organization, (City, ST)'; tags carry schedule and state."""
    raw = clean(entry.get("title"))
    tags = _tags(entry)
    location = next((tag for tag in tags if state_code(tag)), "")
    # The location is the last parenthetical, sometimes followed by a note like "– Multiple Vacancies".
    match = re.search(r"\(([^()]*)\)\s*(?:[–—-]\s*([^()]*))?$", raw)
    note = ""
    if match:
        location, raw = match.group(1).replace("|", ","), raw[:match.start()]
        note = f" ({match.group(2).strip()})" if match.group(2) else ""
    title, organization = raw.strip(" ,"), ""
    if ", " in title:
        title, organization = title.rsplit(", ", 1)
        # "Archivist, University of California, Berkeley" splits one comma too late.
        if re.search(r"\b(University|College) of [A-Z][\w.]*$", title) and ", " in title \
                and len(organization.split()) <= 3:
            title, prefix = title.rsplit(", ", 1)
            organization = f"{prefix}, {organization}"
    body = re.sub(r"^Description:?\s*", "", _body(entry))
    return _job(entry, source, title=title.strip() + note, organization=organization.strip(), location=location,
                salary=extract_salary(body), summary=body, employment=tags)


def parse_dlf(entry, source):
    """CLIR+DLF Job Board. Employer, location, salary, and deadline come from the job page."""
    title = re.sub(r"\s*\(Vacancy\s*#?:?[^)]*\)\s*$", "", clean(entry.get("title")), flags=re.IGNORECASE)
    return _job(entry, source, title=title, summary=_body(entry), employment=_tags(entry))


def parse_code4lib(entry, source):
    """Code4Lib Jobs: the feed has only title and description; the job page has the rest."""
    return _job(entry, source, title=clean(entry.get("title")), summary=_body(entry))


def parse_dpc(entry, source):
    """Digital Preservation Coalition: 'Vacancy for Position at Organization'. Mostly outside the US."""
    title = clean(entry.get("title"))
    if not title.lower().startswith("vacancy for "):
        return None  # pages about advertising, not job posts
    title, organization = title[len("vacancy for "):], ""
    if " at " in title:
        title, organization = title.rsplit(" at ", 1)
    body = _body(entry)
    return _job(entry, source, title=title.strip(), organization=organization.strip(),
                salary=extract_salary(body), summary=body)


PARSERS = {
    "higheredjobs": parse_higheredjobs,
    "madgex": parse_madgex,
    "archivesgig": parse_archivesgig,
    "saa": parse_saa,
    "simmons": parse_simmons,
    "dlf": parse_dlf,
    "code4lib": parse_code4lib,
    "dpc": parse_dpc,
}


class SourceSkipped(Exception):
    """A source that is deliberately not fetched (e.g. missing API key)."""


_USAJOBS_PAY_UNITS = {"PA": "year", "PH": "hour", "PD": "day", "PM": "month", "BW": "2 weeks"}


def parse_usajobs(item, source):
    """One USAJOBS search result (MatchedObjectDescriptor)."""
    details = item.get("UserArea", {}).get("Details", {})
    places = item.get("PositionLocation") or []
    location = places[0].get("LocationName", "") if len(places) == 1 else item.get("PositionLocationDisplay", "")
    pay = (item.get("PositionRemuneration") or [{}])[0]
    salary = format_pay(pay.get("MinimumRange"), pay.get("MaximumRange"),
                        _USAJOBS_PAY_UNITS.get(pay.get("RateIntervalCode", ""), ""))
    employment = [s.get("Name", "") for s in item.get("PositionSchedule", [])]
    employment += [o.get("Name", "") for o in item.get("PositionOfferingType", [])]
    return _job({}, source, title=clean(item.get("PositionTitle")), organization=clean(item.get("OrganizationName")),
                location=location, salary=salary, summary=clean(details.get("JobSummary")),
                deadline=(item.get("ApplicationCloseDate") or "")[:10], employment=employment,
                remote="remote" if details.get("RemoteIndicator") else "",
                url=item.get("PositionURI"), posted=(item.get("PublicationStartDate") or "")[:19])


def fetch_usajobs(source, session) -> list:
    """Federal jobs from the USAJOBS search API (free key: https://developer.usajobs.gov/)."""
    key, email = os.environ.get("USAJOBS_API_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not (key and email):
        raise SourceSkipped("USAJOBS_API_KEY and USAJOBS_EMAIL are not set")
    # USAJOBS asks callers to identify themselves with the email the key was issued to.
    headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    jobs, page, pages = [], 1, 1
    while page <= min(pages, 5):
        params = {"JobCategoryCode": ";".join(source["series"]), "HiringPath": "public",
                  "ResultsPerPage": 500, "Page": page}
        response = session.get(source["url"], params=params, headers=headers, timeout=config.REQUEST_TIMEOUT)
        response.raise_for_status()
        result = response.json()["SearchResult"]
        pages = int(result.get("UserArea", {}).get("NumberOfPages") or 1)
        jobs.extend(parse_usajobs(item.get("MatchedObjectDescriptor", {}), source)
                    for item in result.get("SearchResultItems", []))
        page += 1
    return [job for job in jobs if job["url"] and job["title"]]


def _page_urls(source) -> list:
    """WordPress feeds hold only 10 posts; `pages` fetches older ones too (?paged=2…)."""
    url = source["url"]
    joiner = "&" if "?" in url else "?"
    return [url] + [f"{url}{joiner}paged={n}" for n in range(2, source.get("pages", 1) + 1)]


def fetch_source(source, session) -> list:
    if source["parser"] == "usajobs":
        return fetch_usajobs(source, session)
    parse = PARSERS[source["parser"]]
    jobs = []
    for url in _page_urls(source):
        response = session.get(url, timeout=config.REQUEST_TIMEOUT,
                               headers={"Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"})
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed.bozo and not feed.entries:
            raise ValueError(f"unparseable feed: {feed.bozo_exception}")
        for entry in feed.entries:
            try:
                job = parse(entry, source)
            except Exception:
                log.exception("Could not parse an entry from %s", source["name"])
                continue
            if not job or not (job["url"] and job["title"]):
                continue
            if source.get("title_filter") and not relevant_title(job["title"]):
                continue
            jobs.append(job)
    return jobs


def fetch_all(sources=config.SOURCES):
    """Returns (jobs, results) where results maps source name -> count, or a 'skipped:'/'error:' string."""
    session = requests.Session()
    session.headers["User-Agent"] = config.USER_AGENT
    jobs, results = [], {}
    for i, source in enumerate(sources):
        if i:
            time.sleep(config.FEED_DELAY_SECONDS)
        try:
            found = fetch_source(source, session)
            results[source["name"]] = len(found)
            jobs.extend(found)
            log.info("%-45s %4d jobs", source["name"], len(found))
        except SourceSkipped as exc:
            results[source["name"]] = f"skipped: {exc}"
            log.info("%-45s skipped: %s", source["name"], exc)
        except Exception as exc:
            results[source["name"]] = f"error: {exc}"
            log.error("%-45s failed: %s", source["name"], exc)
    return jobs, results
