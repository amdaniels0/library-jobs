"""Read schema.org JobPosting data from job detail pages, politely and once per job."""
import json
import logging
import re
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from . import config
from .text import US_STATES, clean, extract_salary, format_pay, job_types, parse_location, parse_us_date, state_code

log = logging.getLogger(__name__)


def _items(data):
    if isinstance(data, list):
        for item in data:
            yield from _items(item)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _items(data["@graph"])


def _is_posting(item) -> bool:
    kind = item.get("@type")
    return kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind)


def _as_list(value):
    if isinstance(value, str) and value.startswith("["):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _salary(base) -> str:
    if isinstance(base, str):
        return extract_salary(base)
    if not isinstance(base, dict):
        return ""
    value = base.get("value", base)
    if not isinstance(value, dict):
        return extract_salary(f"${value}")
    unit = {"HOUR": "hour", "YEAR": "year", "MONTH": "month", "WEEK": "week"}.get(
        str(value.get("unitText", "")).upper(), "")
    return format_pay(value.get("minValue") or value.get("value"), value.get("maxValue"), unit)


def _microdata_posting(soup):
    """Pages marked up with itemprop attributes (e.g. Code4Lib), shaped like a JSON-LD JobPosting."""
    root = soup.find(itemtype=re.compile(r"schema\.org/JobPosting$"))
    if not root:
        return None

    def prop(name, scope=root):
        tag = scope.find(itemprop=name)
        return (tag.get("content") or tag.get_text(" ", strip=True)) if tag else ""

    org = root.find(itemprop="hiringOrganization")
    place = root.find(itemprop="jobLocation")
    return {
        "@type": "JobPosting",
        "datePosted": prop("datePosted"),
        "validThrough": prop("validThrough"),
        "employmentType": prop("employmentType"),
        "baseSalary": prop("baseSalary") or prop("estimatedSalary"),
        "hiringOrganization": {"name": prop("name", org) or org.get_text(" ", strip=True)} if org else None,
        "jobLocation": {"address": {"addressLocality": prop("addressLocality", place),
                                    "addressRegion": prop("addressRegion", place)}} if place else None,
    }


def _labelled_rows(soup) -> dict:
    """Two-cell table rows like 'Location | Washington D.C.' (WPJobBoard sites such as CLIR+DLF)."""
    rows = {}
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) == 2:
            rows.setdefault(clean(cells[0].get_text(" ")).rstrip(":").lower(), clean(cells[1].get_text(" ")))
    return rows


def parse_jobposting(html: str) -> dict:
    """Extract the fields we use from the page's JobPosting data (JSON-LD or microdata) and labelled fields."""
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        posting = next((item for item in _items(data) if _is_posting(item)), None)
        if posting:
            found = _from_posting(posting)
            break
    else:
        posting = _microdata_posting(soup)
        if posting:
            found = _from_posting(posting)

    rows = _labelled_rows(soup)
    low = next((v for k, v in rows.items() if k.startswith("minimum compensation")), "")
    high = next((v for k, v in rows.items() if k.startswith("maximum compensation")), "")
    if low and "salary" not in found:
        unit = "hour" if rows.get("hourly or salary?", "").lower() == "hourly" else "year"
        found["salary"] = format_pay(*(re.sub(r"[^\d.]", "", v) for v in (low, high)), unit)
    if rows.get("location") and not found.get("state"):
        found["location"], found["state"] = parse_location(rows["location"])
    return {k: v for k, v in found.items() if v}


def _from_posting(posting) -> dict:
    found = {}
    if re.match(r"\d{4}-\d{2}-\d{2}", str(posting.get("validThrough", ""))):
        found["deadline"] = str(posting["validThrough"])[:10]
    posted = str(posting.get("datePosted") or "")
    if posted:
        found["posted_date"] = posted if re.match(r"\d{4}-\d{2}-\d{2}", posted) else parse_us_date(posted)
    types = job_types("", _as_list(posting.get("employmentType")))
    if types:
        found["job_type"] = ", ".join(types)
    salary = _salary(posting.get("baseSalary"))
    if salary:
        found["salary"] = salary
    org = posting.get("hiringOrganization")
    if isinstance(org, dict) and org.get("name"):
        found["organization"] = clean(org["name"])
    for place in _as_list(posting.get("jobLocation")):
        address = place.get("address", {}) if isinstance(place, dict) else {}
        if not isinstance(address, dict):
            continue
        city = clean(address.get("addressLocality"))
        region = clean(address.get("addressRegion"))
        country = clean(address.get("addressCountry"))
        code = state_code(region)
        if code:
            found["location"] = f"{city}, {code}" if city else US_STATES[code]
            found["state"] = code
        elif city or region or country:
            found["location"], found["state"] = parse_location(", ".join(p for p in (city, region, country) if p))
        break
    if "TELECOMMUTE" in str(posting.get("jobLocationType", "")).upper():
        found["remote"] = "remote"
    return found


class DetailFetcher:
    def __init__(self, delay=config.DETAIL_DELAY_SECONDS):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self._robots = {}
        self._last_request = {}

    def allowed(self, url: str) -> bool:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            parser = RobotFileParser()
            try:
                response = self.session.get(base + "/robots.txt", timeout=config.REQUEST_TIMEOUT)
                parser.parse(response.text.splitlines() if response.ok else [])
            except requests.RequestException as exc:
                log.warning("Could not read robots.txt for %s: %s", base, exc)
                parser.parse([])
            self._robots[base] = parser
        return self._robots[base].can_fetch(config.USER_AGENT, url)

    def fetch(self, url: str) -> dict:
        """Returns enrichment fields; raises on network/HTTP errors."""
        if not self.allowed(url):
            raise PermissionError("disallowed by robots.txt")
        host = urlparse(url).netloc
        wait = self.delay - (time.monotonic() - self._last_request.get(host, -1e9))
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.monotonic()
        response = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
        response.raise_for_status()
        return parse_jobposting(response.text)
