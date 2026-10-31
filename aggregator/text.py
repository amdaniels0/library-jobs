"""Text cleanup and field extraction shared by all sources."""
import html
import re
from datetime import datetime, timezone
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


_DC_RE = re.compile(r"\bWashington,?\s+D\.?\s?C\.?(?=,|/|\s|$)", re.IGNORECASE)
# "Irvine, California (US)", "Washington, DC/Remote", "Bloomington, Indiana or remote"
_LOCATION_NOISE_RE = re.compile(r"\s*\((?:US|USA|U\.S\.A?\.?)\)|\s*(?:/|\bor\b)\s*remote\b.*$", re.IGNORECASE)


def parse_location(raw: str) -> tuple:
    """Normalize a location string. Returns (display, state_code)."""
    text = _LOCATION_NOISE_RE.sub("", _DC_RE.sub("Washington, DC", clean(raw))).strip(" ,")
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
    # "Virginia, Richmond": a full state name in another position (not two-letter codes, which are ambiguous).
    for i, part in enumerate(parts[:-1]):
        code = _STATE_BY_NAME.get(part.lower())
        if code and not country_of(", ".join(parts)):
            return f"{', '.join(parts[:i] + parts[i + 1:])}, {code}", code
    return ", ".join(parts), ""


# Countries seen in (or plausible for) postings, by lowercase name, alias, or two-letter code.
# US state codes are checked first, so codes like CA (California) never reach this table.
_COUNTRIES = {
    "canada": "Canada", "united kingdom": "United Kingdom", "uk": "United Kingdom", "gb": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom", "scotland": "United Kingdom",
    "wales": "United Kingdom", "northern ireland": "United Kingdom", "ireland": "Ireland", "ie": "Ireland",
    "australia": "Australia", "au": "Australia", "new zealand": "New Zealand", "nz": "New Zealand",
    "hong kong": "Hong Kong", "hk": "Hong Kong", "china": "China", "cn": "China", "taiwan": "Taiwan",
    "tw": "Taiwan", "japan": "Japan", "jp": "Japan", "south korea": "South Korea", "korea": "South Korea",
    "kr": "South Korea", "singapore": "Singapore", "sg": "Singapore", "india": "India", "malaysia": "Malaysia",
    "thailand": "Thailand", "vietnam": "Vietnam", "philippines": "Philippines", "indonesia": "Indonesia",
    "germany": "Germany", "france": "France", "fr": "France", "netherlands": "Netherlands", "nl": "Netherlands",
    "belgium": "Belgium", "be": "Belgium", "switzerland": "Switzerland", "ch": "Switzerland", "italy": "Italy",
    "it": "Italy", "spain": "Spain", "es": "Spain", "portugal": "Portugal", "austria": "Austria", "at": "Austria",
    "sweden": "Sweden", "se": "Sweden", "norway": "Norway", "no": "Norway", "denmark": "Denmark", "dk": "Denmark",
    "finland": "Finland", "fi": "Finland", "iceland": "Iceland", "estonia": "Estonia", "poland": "Poland",
    "czech republic": "Czech Republic", "hungary": "Hungary", "greece": "Greece", "turkey": "Turkey",
    "armenia": "Armenia", "am": "Armenia", "israel": "Israel", "il": "Israel", "lebanon": "Lebanon",
    "lb": "Lebanon", "qatar": "Qatar", "qa": "Qatar", "united arab emirates": "United Arab Emirates",
    "uae": "United Arab Emirates", "ae": "United Arab Emirates", "abu dhabi": "United Arab Emirates",
    "dubai": "United Arab Emirates", "saudi arabia": "Saudi Arabia", "sa": "Saudi Arabia", "egypt": "Egypt",
    "eg": "Egypt", "south africa": "South Africa", "za": "South Africa", "kenya": "Kenya", "nigeria": "Nigeria",
    "ghana": "Ghana", "mexico": "Mexico", "mx": "Mexico", "brazil": "Brazil", "br": "Brazil",
    "argentina": "Argentina", "chile": "Chile", "colombia": "Colombia", "peru": "Peru", "jamaica": "Jamaica",
    # Canadian provinces (NL and SK are left out: they are also country codes).
    "ontario": "Canada", "on": "Canada", "quebec": "Canada", "qc": "Canada", "british columbia": "Canada",
    "bc": "Canada", "alberta": "Canada", "ab": "Canada", "manitoba": "Canada", "mb": "Canada",
    "saskatchewan": "Canada", "nova scotia": "Canada", "ns": "Canada", "new brunswick": "Canada", "nb": "Canada",
    "newfoundland and labrador": "Canada", "prince edward island": "Canada", "pe": "Canada",
}


def country_of(location: str) -> str:
    """The non-US country named in a location string ('Kunshan, Jiangsu, China', 'Lebanon (LB)', 'AE'), or ''."""
    text = clean(location)
    parts = re.split(r"\s*[,()]\s*", text)
    for part in reversed([p.strip(" .").lower() for p in parts if p.strip(" .")]):
        if part in _COUNTRIES and not (len(part) == 2 and part.upper() in US_STATES):
            return _COUNTRIES[part]
    return ""


def location_bucket(location: str, state: str, us_only_source: bool = False) -> tuple:
    """Classify a job's location: ('state', 'PA'), ('country', 'Canada'), ('us', ''), or ('none', '')."""
    if not state:
        # Locations stored before a parser fix get another chance ("Bloomington, Indiana or remote").
        _, state = parse_location(location)
    if state:
        return "state", state
    country = country_of(location)
    if country:
        return "country", country
    if us_only_source or re.search(r"\bunited states\b|\(us\)", location or "", re.IGNORECASE):
        return "us", ""
    return "none", ""


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


_FAMILIES = [
    ("Faculty & teaching", r"\bprofessor|\blecturer|\binstructor\b|\bfaculty\b|\bpostdoc|\bteaching\b"),
    ("Leadership", r"\bdean\b|\bdirector\b|\bhead\b|\bchair\b|\bchief\b|university librarian|\bAUL\b"),
    ("Archives & special collections", r"archiv|special collections|manuscript|rare book|\brecords\b"),
    ("Museums & curatorial", r"museum|curat|exhibit|\bgallery\b|collections manager|registrar|visitor (experience|services)"),
    # Mostly from HigherEdJobs' broad Arts & Museum Administration category.
    ("Performing arts & events", r"theat|perform|\bstage\b|props|concert|music|\bevents?\b|ticket|box office|venue"),
    ("Administration & operations", r"marketing|communications|business|financ|budget|fundrais|advancement|"
                                    r"development (officer|associate|coordinator)|human resources|operations|administrative"),
    ("Conservation & preservation", r"\bconservat(or|ion)|preservation"),
    ("Metadata & technical services", r"metadata|catalog|technical services|acquisition|electronic resources|"
                                      r"e-resources|collection development|collection strategy"),
    ("Digital, data & technology", r"digital|\bdata\b|systems|technolog|developer|software|\bweb\b|\bGIS\b|"
                                   r"informatics|analyst|engineer|computing|\bIT\b|\bAI\b"),
    ("Research, instruction & outreach", r"reference|instruction|research|liaison|subject|outreach|engagement|"
                                         r"learning|scholarly|literacy|student success|education"),
    ("Library staff & access services", r"assistant|associate|technician|circulation|access services|clerk|"
                                        r"specialist|\bpage\b|\baide\b"),
    ("Librarian, other", r"librar"),
]
_FAMILY_RES = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _FAMILIES]


def job_family(title: str) -> str:
    """Rough job family from the title, first match wins (so 'Director of Archives' is Leadership)."""
    for name, pattern in _FAMILY_RES:
        if pattern.search(title or ""):
            return name
    return "Other"


def relevant_title(title: str) -> bool:
    """Keyword searches on general job boards also match unrelated jobs; keep library/archives/museum work."""
    return bool(_RELEVANT_TITLE_RE.search(title or ""))


def parse_time(value):
    """ISO date or datetime string -> timezone-aware datetime (UTC if none given), or None."""
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


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
