import time

from aggregator import config
from aggregator.sources import parse_archivesgig, parse_higheredjobs, parse_madgex, parse_saa
from aggregator.text import canonical_url

SOURCE = {s["parser"]: s for s in reversed(config.SOURCES)}


def entry(**fields):
    fields.setdefault("link", "https://example.org/job/1")
    fields.setdefault("published_parsed", time.strptime("2026-10-30 12:00", "%Y-%m-%d %H:%M"))
    return fields


def test_canonical_url_strips_tracking_only():
    assert canonical_url(
        "https://jobs.chronicle.com/job/38057183/archivist/?TrackID=10&utm_source=rss&utm_medium=feed"
    ) == "https://jobs.chronicle.com/job/38057183/archivist/"
    assert canonical_url("https://www.higheredjobs.com/details.cfm?JobCode=179603956") == \
        "https://www.higheredjobs.com/details.cfm?JobCode=179603956"


def test_higheredjobs():
    job = parse_higheredjobs(entry(title="Research &amp; Instruction Librarian",
                                   summary="Iowa State University (Ames, IA)"), SOURCE["higheredjobs"])
    assert job["title"] == "Research & Instruction Librarian"
    assert job["organization"] == "Iowa State University"
    assert (job["location"], job["state"]) == ("Ames, IA", "IA")
    assert job["posted_date"].startswith("2026-10-30T12:00")


def test_higheredjobs_org_with_parentheses():
    job = parse_higheredjobs(entry(title="Archivist", summary="University of Maryland (UMD) (College Park, MD)"),
                             SOURCE["higheredjobs"])
    assert job["organization"] == "University of Maryland (UMD)"
    assert job["state"] == "MD"


def test_madgex_chronicle():
    summary = ("Salary Not specified:\n\nSarah Lawrence College:\nCollege Archivist\n\nJob Description\n"
               "Position Overview\nThe Sarah Lawrence College Library invites applications.\nNew York, United States")
    job = parse_madgex(entry(title="Sarah Lawrence College: College Archivist", summary=summary), SOURCE["madgex"])
    assert job["title"] == "College Archivist"
    assert job["organization"] == "Sarah Lawrence College"
    assert job["salary"] == ""
    assert job["state"] == "NY"
    assert job["summary"].startswith("Job Description Position Overview")


def test_madgex_salary_line():
    summary = "$58,000 - $62,000:\nLamar University:\nLeads the archives.\nBeaumont, TX, TX"
    job = parse_madgex(entry(title="Lamar University: Director of Archives", summary=summary), SOURCE["madgex"])
    assert job["salary"] == "$58,000 - $62,000"
    assert job["location"] == "Beaumont, TX"


def test_archivesgig_uses_labelled_fields():
    body = ("Job Opening Title:Archivist (NPS, Aztec Ruins National Monument) Name of Employer:Great Basin Institute "
            "Job Site Location (City AND State):Albuquerque, New Mexico Application Deadline:11/19/2026 "
            "Minimum Degree Requirement:MLIS Salary Range:$30.00/hour Job Opening URL:https://example.org/apply")
    job = parse_archivesgig(entry(
        title="Albuquerque, NM: Archivist – NPS, Aztec Ruins National Monument (term), Great Basin Institute",
        summary=body), SOURCE["archivesgig"])
    assert job["organization"] == "Great Basin Institute"
    assert job["title"] == "Archivist – NPS, Aztec Ruins National Monument (term)"
    assert job["state"] == "NM"
    assert job["deadline"] == "2026-11-19"
    assert job["salary"] == "$30.00/hour"
    assert job["job_type"] == "Temporary"


def test_saa_title_and_location():
    job = parse_saa(entry(title="Senior Archivist | Barnard College",
                          summary="New York, New York,  Reporting to the Director of Archives, the Senior Archivist…"),
                    SOURCE["saa"])
    assert job["title"] == "Senior Archivist"
    assert job["organization"] == "Barnard College"
    assert (job["location"], job["state"]) == ("New York, NY", "NY")
    assert job["summary"].startswith("Reporting to")
