"""Settings and job sources. Paths can be overridden with environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("JOBS_DB", ROOT / "data" / "jobs.db"))
SITE_DIR = Path(os.environ.get("JOBS_SITE_DIR", ROOT / "_site"))

SITE_URL = "https://amdaniels0.github.io/library-jobs/"
REPO_URL = "https://github.com/amdaniels0/library-jobs"
SITE_TITLE = "Library & Archives Jobs"
SITE_DESCRIPTION = (
    "Library, archives, museum, and information science jobs gathered "
    "from several job boards, updated twice a day."
)
TIMEZONE = "America/New_York"

USER_AGENT = f"LibraryJobsAggregator/2.0 (+{REPO_URL})"
REQUEST_TIMEOUT = 30
FEED_DELAY_SECONDS = 1.0

# A job drops off the site when no feed has listed it for this many days,
# or when its application deadline has passed.
EXPIRY_DAYS = 60
NEW_DAYS = 7
RSS_MAX_ITEMS = 500

# Detail pages are fetched once per job, only on boards whose pages carry
# schema.org JobPosting data. HigherEdJobs blocks automated page requests.
DETAIL_DOMAINS = {"jobs.chronicle.com", "careers.insidehighered.com", "jobs.diglib.org", "jobs.code4lib.org"}
DETAIL_LIMIT_PER_RUN = 80
DETAIL_DELAY_SECONDS = 3.0
DETAIL_MAX_ATTEMPTS = 3

# Health check: refuse to publish if fetching mostly failed.
MIN_SOURCES_OK = 10
MIN_JOBS_FETCHED = 200

# Federal job series for USAJOBS: https://data.usajobs.gov/api/codelist/occupationalseries
USAJOBS_SERIES = [
    "1410",  # Librarian
    "1411",  # Library Technician
    "1412",  # Technical Information Services
    "1420",  # Archivist
    "1421",  # Archives Technician
    "1499",  # Library and Archives Student Trainee
    "1015",  # Museum Curator
    "1016",  # Museum Specialist and Technician
    "1010",  # Exhibits Specialist
    "0308",  # Records and Information Management
]


def _keyword_feeds(board, host, label):
    """Keyword searches on the Madgex boards; title_filter drops unrelated matches (e.g. faculty jobs)."""
    keywords = {"Archivist": "archivist", "Curator": "curator", "Museum": "museum",
                "Digital Preservation": "digital+preservation", "Metadata": "metadata"}
    return [{"name": f"{label} {name}", "board": board, "parser": "madgex", "title_filter": True,
             "url": f"https://{host}/jobsrss/?keywords={query}&countrycode=US"} for name, query in keywords.items()]

# `name` is stored with each listing, so keep names stable.
# `board` is what the site shows and filters on.
SOURCES = [
    {"name": "HigherEdJobs Arts and Museum Administration", "board": "HigherEdJobs", "parser": "higheredjobs",
     "url": "https://www.higheredjobs.com/rss/categoryFeed.cfm?catID=36"},
    {"name": "HigherEdJobs Libraries", "board": "HigherEdJobs", "parser": "higheredjobs",
     "url": "https://www.higheredjobs.com/rss/categoryFeed.cfm?catID=34"},
    {"name": "HigherEdJobs Library and Information Science", "board": "HigherEdJobs", "parser": "higheredjobs",
     "url": "https://www.higheredjobs.com/rss/categoryFeed.cfm?catID=182"},
    {"name": "HigherEdJobs Data Science", "board": "HigherEdJobs", "parser": "higheredjobs",
     "url": "https://www.higheredjobs.com/rss/categoryFeed.cfm?catID=290"},
    {"name": "Chronicle Library & Library Administration", "board": "Chronicle", "parser": "madgex",
     "url": "https://jobs.chronicle.com/jobsrss/?PositionType=111&countrycode=US"},
    {"name": "IHE Libraries", "board": "Inside Higher Ed", "parser": "madgex",
     "url": "https://careers.insidehighered.com/jobsrss/?keywords=libraries&countrycode=US"},
    {"name": "IHE Library", "board": "Inside Higher Ed", "parser": "madgex",
     "url": "https://careers.insidehighered.com/jobsrss/?keywords=library&countrycode=US"},
    {"name": "IHE Archives", "board": "Inside Higher Ed", "parser": "madgex",
     "url": "https://careers.insidehighered.com/jobsrss/?keywords=archives&countrycode=US"},
    {"name": "IHE Information Science", "board": "Inside Higher Ed", "parser": "madgex",
     "url": "https://careers.insidehighered.com/jobsrss/?keywords=information+science&countrycode=US"},
    {"name": "Archivesgig", "board": "Archives Gig", "parser": "archivesgig",
     "url": "https://archivesgig.com/feed/"},
    {"name": "SAA Careers", "board": "SAA Careers", "parser": "saa",
     "url": "https://careers.archivists.org/jobs?display=rss"},
    *_keyword_feeds("Chronicle", "jobs.chronicle.com", "Chronicle"),
    *_keyword_feeds("Inside Higher Ed", "careers.insidehighered.com", "IHE"),
    {"name": "Simmons SLIS Jobline", "board": "Simmons Jobline", "parser": "simmons", "pages": 3,
     "url": "https://slis-jobline.simmons.edu/feed/"},
    {"name": "CLIR+DLF Job Board", "board": "CLIR+DLF", "parser": "dlf",
     "url": "https://jobs.diglib.org/wpjobboard/xml/rss/?filter=active"},
    {"name": "Code4Lib Jobs", "board": "Code4Lib", "parser": "code4lib",
     "url": "https://jobs.code4lib.org/jobs.atom"},
    {"name": "Digital Preservation Coalition", "board": "Digital Preservation Coalition", "parser": "dpc",
     "url": "https://www.dpconline.org/news/job-vacancies?format=feed&type=rss"},
    # Needs USAJOBS_API_KEY and USAJOBS_EMAIL (repository secrets); skipped without them.
    {"name": "USAJOBS", "board": "USAJOBS", "parser": "usajobs", "series": USAJOBS_SERIES,
     "url": "https://data.usajobs.gov/api/search"},
]

BOARD_BY_SOURCE = {s["name"]: s["board"] for s in SOURCES}
# Legacy rows from the Pi database used this source name.
BOARD_BY_SOURCE["ALA Joblist"] = "ALA JobLIST"
