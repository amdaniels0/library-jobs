"""Text cleanup and field extraction shared by all sources."""
import html
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands",
}
_STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}
_COUNTRY_SUFFIXES = {"united states", "united states of america", "usa", "us", "u.s.", "u.s.a."}


def clean(value) -> str:
    """Decode entities (sometimes double-encoded), drop tags, collapse whitespace."""
    if not value:
        return ""
    text = str(value)
    for _ in range(2):
        text = html.unescape(text)
    if "<" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def clean_lines(value) -> list:
    """Like clean(), but keep line breaks (from newlines or <br>/<p>) as separate items."""
    if not value:
        return []
    text = html.unescape(str(value))
    if "<" in text:
        text = BeautifulSoup(text, "html.parser").get_text("\n")
    return [line for line in (clean(part) for part in text.split("\n")) if line]


_TRACKING_PARAMS = re.compile(r"^(utm_\w+|trackid|source|ref)$", re.IGNORECASE)


def canonical_url(url: str) -> str:
    """Drop feed tracking parameters so the same posting always has the same URL."""
    parts = urlsplit((url or "").strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAMS.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def truncate(text: str, limit: int = 300) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + "…"


_MONEY = r"\$\s?\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\$\s?\d+(?:\.\d{1,2})?\s?[kK]?"
_PERIOD = (
    r"(?:\s*(?:/|per|an|a)\s*(?:hour|hr|year|yr|annum|month|mo|week)\b"
    r"|\s*(?:annually|hourly|monthly|yearly|annual)\b)?"
)
_SALARY_RE = re.compile(
    rf"(?:{_MONEY})(?:\s*(?:-|–|—|to)\s*(?:{_MONEY}))?{_PERIOD}", re.IGNORECASE
)


def extract_salary(text: str) -> str:
    """Return the first plausible pay figure or range in text, e.g. '$52,000 - $60,000/year'."""
    if not text:
        return ""
    for match in _SALARY_RE.finditer(text):
        amount = match.group(0).strip()
        digits = re.sub(r"[^\d.]", "", amount.split()[0].split("-")[0].split("–")[0])
        try:
            value = float(digits.rstrip("."))
        except ValueError:
            continue
        # Skip application fees and the like; keep hourly rates and "50k".
        if value >= 7 or re.search(r"\d\s?[kK]", amount):
            return re.sub(r"\s+", " ", amount)
    return ""


def format_pay(low, high=None, unit="") -> str:
    """format_pay(55000, 65000, 'year') -> '$55,000 - $65,000/year'."""
    def money(amount):
        amount = float(amount)
        return f"${amount:,.2f}" if amount < 1000 and amount % 1 else f"${amount:,.0f}"

    try:
        values = [float(v) for v in (low, high) if v not in (None, "", 0, "0")]
    except (TypeError, ValueError):
        return ""
    if not values:
        return ""
    # Employers sometimes pick the wrong unit on posting forms ("Hourly" for $95,864).
    if unit == "hour" and max(values) >= 1000:
        unit = "year"
    elif unit == "year" and max(values) < 300:
        unit = "hour"
    suffix = f"/{unit}" if unit else ""
    if len(values) == 2 and values[0] != values[1]:
        return f"{money(values[0])} - {money(values[1])}{suffix}"
    return f"{money(values[0])}{suffix}"


def state_code(token: str) -> str:
    token = token.replace(".", "").strip()
    if token.upper() in US_STATES:
        return token.upper()
    return _STATE_BY_NAME.get(token.lower(), "")


_DC_RE = re.compile(r"\bWashington,?\s+D\.?\s?C\.?(?=,|$)", re.IGNORECASE)


def parse_location(raw: str) -> tuple:
    """Normalize a location string. Returns (display, state_code)."""
    text = _DC_RE.sub("Washington, DC", clean(raw))
    if not text:
        return "", ""
    parts = [p for p in (p.strip() for p in text.split(",")) if p and p.lower() not in _COUNTRY_SUFFIXES]
    if not parts:
        return "United States", ""
    # "Washington, DC, Washington, DC": the whole location repeated.
    half = len(parts) // 2
    if len(parts) % 2 == 0 and half >= 2 and [p.lower() for p in parts[:half]] == [p.lower() for p in parts[half:]]:
        parts = parts[:half]
    # "Beaumont, TX, TX" and "Beaumont, Texas, TX" both mean one state,
    # but "New York, New York" is a city and a state.
    while (len(parts) >= 2 and state_code(parts[-1]) and state_code(parts[-1]) == state_code(parts[-2])
           and (len(parts) >= 3 or parts[-2].upper() in US_STATES)):
        parts.pop()
    code = state_code(parts[-1])
    if code:
        city = ", ".join(parts[:-1])
        if not city:
            return US_STATES[code], code
        return f"{city}, {code}", code
    return ", ".join(parts), ""


_REMOTE_RE = re.compile(r"\b(fully remote|remote|telework|work from home)\b", re.IGNORECASE)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)


def remote_status(*texts) -> str:
    joined = " ".join(t for t in texts if t)
    if _HYBRID_RE.search(joined):
        return "hybrid"
    if _REMOTE_RE.search(joined):
        return "remote"
    return ""


_TYPE_PATTERNS = [
    ("Internship", re.compile(r"\bintern(ship)?s?\b|\bpracticum\b", re.IGNORECASE)),
    ("Fellowship", re.compile(r"\bfellow(ship)?s?\b|\bresidency\b", re.IGNORECASE)),
    ("Part-time", re.compile(r"(?i:\bpart[- ]?time\b)|\bPT\b")),
    ("Temporary", re.compile(
        r"\b(term|temporary|temp|visiting|limited[- ]term|fixed[- ]term|contract|"
        r"grant[- ]funded|project)\b(?! (manager|management|coordinator|lead|director))",
        re.IGNORECASE)),
]
_EMPLOYMENT_TYPES = {
    "FULL_TIME": "Full-time", "PART_TIME": "Part-time", "TEMPORARY": "Temporary", "TERM": "Temporary",
    "CONTRACTOR": "Temporary", "INTERN": "Internship", "INTERNSHIP": "Internship", "INTERNSHIPS": "Internship",
    "FELLOWSHIP": "Fellowship", "PER_DIEM": "Part-time",
}


def employment_label(raw) -> str:
    """'FULL_TIME', 'Full time', 'Full-time Opportunity', 'Temporary/Term-Limited' -> a site label."""
    key = re.sub(r"[^A-Z]+", "_", str(raw).upper()).strip("_")
    for prefix, label in _EMPLOYMENT_TYPES.items():
        if key == prefix or key.startswith(prefix + "_"):
            return label
    return ""


def job_types(title: str, employment_types=()) -> list:
    found = []
    for raw in employment_types or ():
        label = employment_label(raw)
        if label and label not in found:
            found.append(label)
    for label, pattern in _TYPE_PATTERNS:
        if label not in found and pattern.search(title or ""):
            found.append(label)
    if "Part-time" in found and "Full-time" in found:
        found.remove("Full-time")
    return found


_RELEVANT_TITLE_RE = re.compile(
    r"librar|archiv|museum|curat|collection|catalog|metadata|preserv|\bconservat(or|ion)\b|digitiz|"
    r"digital (scholarship|humanities|asset|initiatives|stewardship|collections|projects)|repositor|"
    r"scholarly (publishing|communication)|exhibit|historic|information scien|records manag",
    re.IGNORECASE)


def relevant_title(title: str) -> bool:
    """Keyword searches on general job boards also match unrelated jobs; keep library/archives/museum work."""
    return bool(_RELEVANT_TITLE_RE.search(title or ""))


def parse_us_date(text: str) -> str:
    """'11/19/2026' or 'November 19, 2026' -> '2026-11-19'."""
    text = (text or "").strip()
    for pattern, fmt in ((r"\b\d{1,2}/\d{1,2}/\d{4}\b", "%m/%d/%Y"),
                         (r"\b[A-Z][a-z]+ \d{1,2}, \d{4}\b", "%B %d, %Y")):
        match = re.search(pattern, text)
        if match:
            try:
                return datetime.strptime(match.group(0), fmt).date().isoformat()
            except ValueError:
                pass
    return ""


_REQ_NUMBER_RE =re.compile(r"\(?\b(?:req(?:uisition)?|job|posting)?\s*#\s*[\w-]+\)?|\(\s*[A-Z]*\d{4,}\s*\)", re.IGNORECASE)


def _normalize(text: str) -> str:
    text = clean(text).lower()
    text = _REQ_NUMBER_RE.sub(" ", text)
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"^the ", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def dedupe_key(title: str, organization: str, url: str) -> str:
    """Same job posted on two boards should get the same key."""
    org = _normalize(organization)
    if not org:
        # Without an employer, a title like "Library Assistant" is too generic to merge on.
        return "url:" + url
    return f"{_normalize(title)}|{org}"
