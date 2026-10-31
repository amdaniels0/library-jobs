import time
from datetime import datetime, timezone

import pytest

from aggregator import config, db, sources
from aggregator.details import parse_jobposting
from aggregator.sources import parse_code4lib, parse_dlf, parse_dpc, parse_simmons, parse_usajobs
from aggregator.text import (employment_label, format_pay, job_family, location_bucket, parse_location,
                             relevant_title)

SOURCE = {s["parser"]: s for s in reversed(config.SOURCES)}
NOW = datetime(2026, 10, 31, 14, 0, tzinfo=timezone.utc)


def entry(**fields):
    fields.setdefault("link", "https://example.org/job/1")
    fields.setdefault("published_parsed", time.strptime("2026-10-30 12:00", "%Y-%m-%d %H:%M"))
    return fields


def tags(*terms):
    return [{"term": t} for t in terms]


@pytest.mark.parametrize("title, expected", [
    ("Library Director, Silsby Free Library, (Charlestown, NH)",
     ("Library Director", "Silsby Free Library", "Charlestown, NH")),
    ("Supervising Librarian, New Haven Free Public Library (New Haven, CT)",
     ("Supervising Librarian", "New Haven Free Public Library", "New Haven, CT")),
    ("Assistant Professor, Library Science, Kent State University, (Kent, OH)",
     ("Assistant Professor, Library Science", "Kent State University", "Kent, OH")),
    ("Archivist, University of California, Berkeley (Berkeley, CA)",
     ("Archivist", "University of California, Berkeley", "Berkeley, CA")),
    ("Plans Library Intern, Mass DCR Plans Library, (Boston | MA) – Multiple Vacancies",
     ("Plans Library Intern (Multiple Vacancies)", "Mass DCR Plans Library", "Boston, MA")),
])
def test_simmons_titles(title, expected):
    job = parse_simmons(entry(title=title, tags=tags("Full Time", "librarian")), SOURCE["simmons"])
    assert (job["title"], job["organization"], job["location"]) == expected
    assert job["job_type"].startswith("Full-time")


def test_simmons_state_tag_when_title_has_no_location():
    job = parse_simmons(entry(title="Archives Assistant, Historic New England",
                              tags=tags("Part Time", "Massachusetts"),
                              content=[{"value": "<p>Description: Pay is $22/hour.</p>"}]), SOURCE["simmons"])
    assert job["state"] == "MA"
    assert job["salary"] == "$22/hour"
    assert job["job_type"] == "Part-time"
    assert job["summary"] == "Pay is $22/hour."


def test_dlf_strips_vacancy_number_and_reads_tags():
    job = parse_dlf(entry(title="Librarian (Collection Development Librarian) (Vacancy#:VAR003458)",
                          tags=tags("Full-time", "Temporary/Term-Limited", "Public or Governmental")),
                    SOURCE["dlf"])
    assert job["title"] == "Librarian (Collection Development Librarian)"
    assert job["job_type"] == "Full-time, Temporary"
    assert job["organization"] == ""  # filled from the job page later


def test_code4lib_atom_entry_uses_updated_date():
    e = {"link": "https://jobs.code4lib.org/jobs/66792-slt-systems-analyst", "title": "SLT Systems Analyst",
         "updated_parsed": time.strptime("2026-10-30 16:36", "%Y-%m-%d %H:%M"),
         "content": [{"value": "<p>Brown University Library is hiring…</p>"}]}
    job = parse_code4lib(e, SOURCE["code4lib"])
    assert job["posted_date"].startswith("2026-10-30T16:36")
    assert job["summary"] == "Brown University Library is hiring…"


def test_dpc_vacancy_titles_and_non_vacancies():
    job = parse_dpc(entry(title="Vacancy for Lecturer in Archives and Records Management at University of Liverpool"),
                    SOURCE["dpc"])
    assert (job["title"], job["organization"]) == ("Lecturer in Archives and Records Management",
                                                   "University of Liverpool")
    assert parse_dpc(entry(title="Job advertisements"), SOURCE["dpc"]) is None


def test_usajobs_result():
    item = {
        "PositionTitle": "Librarian (Collection Development)", "OrganizationName": "Library of Congress",
        "PositionURI": "https://www.usajobs.gov:443/GetJob/ViewDetails/890665400",
        "PositionLocation": [{"LocationName": "Washington, District of Columbia"}],
        "PositionRemuneration": [{"MinimumRange": "70623.0", "MaximumRange": "91815.0", "RateIntervalCode": "PA"}],
        "PositionSchedule": [{"Name": "Full-time"}], "PositionOfferingType": [{"Name": "Term"}],
        "PublicationStartDate": "2026-10-28T00:00:00.0000", "ApplicationCloseDate": "2026-11-18T23:59:59.9970",
        "UserArea": {"Details": {"JobSummary": "Selects materials.", "RemoteIndicator": False}},
    }
    job = parse_usajobs(item, SOURCE["usajobs"])
    assert job["organization"] == "Library of Congress"
    assert (job["location"], job["state"]) == ("Washington, DC", "DC")
    assert job["salary"] == "$70,623 - $91,815/year"
    assert job["deadline"] == "2026-11-18"
    assert job["posted_date"] == "2026-10-28T00:00:00"
    assert job["job_type"] == "Full-time, Temporary"
    assert job["remote"] == ""


def test_usajobs_skipped_without_key(monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    jobs, results = sources.fetch_all([SOURCE["usajobs"]])
    assert jobs == [] and results["USAJOBS"].startswith("skipped")


def test_title_filter():
    assert relevant_title("Processing Archivist")
    assert relevant_title("Curator of Education and Community Engagement")
    assert relevant_title("Digital Stewardship Librarian")
    assert not relevant_title("Provost & Chief Academic Officer")
    assert not relevant_title("Assistant Professor of Practice - Construction Management")
    assert not relevant_title("Conservatory Faculty, Voice")


@pytest.mark.parametrize("location, state, us_only, expected", [
    ("Pittsburgh, PA", "PA", False, ("state", "PA")),
    ("Kunshan, Jiangsu, China", "", False, ("country", "China")),
    ("Lebanon (LB)", "", False, ("country", "Lebanon")),
    ("AE", "", True, ("country", "United Arab Emirates")),
    ("Vancouver, BC, Canada", "", False, ("country", "Canada")),
    ("Toronto, ON", "", False, ("country", "Canada")),
    ("Irvine, California (US)", "", False, ("state", "CA")),  # stored before the parser fix
    ("Georgia Gwinnett College", "", True, ("us", "")),
    ("Multiple Locations", "", True, ("us", "")),
    ("", "", False, ("none", "")),
])
def test_location_bucket(location, state, us_only, expected):
    assert location_bucket(location, state, us_only) == expected


def test_job_family():
    assert job_family("Director of Archives and Special Collections") == "Leadership"
    assert job_family("Assistant Professor of Data Science") == "Faculty & teaching"
    assert job_family("Processing Archivist") == "Archives & special collections"
    assert job_family("Metadata Librarian") == "Metadata & technical services"
    assert job_family("Seasonal Capstone Props Supervisor") == "Performing arts & events"
    assert job_family("Reference and Instruction Librarian") == "Research, instruction & outreach"
    assert job_family("Librarian I") == "Librarian, other"


def test_helpers():
    assert parse_location("Washington D.C., Washington D.C.") == ("Washington, DC", "DC")
    assert parse_location("Washington, District of Columbia") == ("Washington, DC", "DC")
    assert employment_label("Full-time Opportunity") == "Full-time"
    assert employment_label("Temporary/Term-Limited") == "Temporary"
    assert employment_label("Public or Governmental") == ""
    assert format_pay("22.5", None, "hour") == "$22.50/hour"
    assert format_pay(0, None) == ""
    assert format_pay("95864", "102976", "hour") == "$95,864 - $102,976/year"  # mislabelled on the form
    assert format_pay("24", "28", "year") == "$24 - $28/hour"


def test_code4lib_microdata_page():
    html = """<div itemscope itemtype="http://schema.org/JobPosting">
      <h1 itemprop="title">Systems Librarian</h1>
      <span itemprop="employmentType">Full time</span>
      <span itemprop="hiringOrganization" itemscope itemtype="http://schema.org/Organization">
        <a href="/employers/nybg" itemprop="url"><span itemprop="name">New York Botanical Garden</span></a></span>
      <span itemprop="jobLocation" itemscope itemtype="http://schema.org/Place">
        <span itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">
          <span itemprop="addressLocality">Bronx</span> , <span itemprop="addressRegion">NY</span></span></span>
      <span itemprop="estimatedSalary">$77,000</span>
      <span itemprop="datePosted">October 29, 2026</span></div>"""
    assert parse_jobposting(html) == {
        "organization": "New York Botanical Garden", "location": "Bronx, NY", "state": "NY",
        "job_type": "Full-time", "salary": "$77,000", "posted_date": "2026-10-29",
    }


def test_dlf_labelled_table_page():
    html = """<script type="application/ld+json">{"@type": "JobPosting", "validThrough": "2026-12-01",
      "hiringOrganization": {"name": "Library of Congress"},
      "jobLocation": {"address": {"addressLocality": "Washington D.C.", "addressRegion": "Washington D.C."}}}</script>
      <table><tr><td>Location</td><td>Washington D.C., Washington D.C.</td></tr>
      <tr><td>Minimum Compensation in Local Currency</td><td>$70,623</td></tr>
      <tr><td>Maximum Compensation in Local Currency</td><td>$91,815</td></tr>
      <tr><td>Hourly or Salary?</td><td>Salary</td></tr></table>"""
    fields = parse_jobposting(html)
    assert fields["organization"] == "Library of Congress"
    assert (fields["location"], fields["state"]) == ("Washington, DC", "DC")
    assert fields["salary"] == "$70,623 - $91,815/year"
    assert fields["deadline"] == "2026-12-01"


def _job(**overrides):
    base = {"title": "Systems Librarian", "organization": "", "location": "", "state": "", "remote": "",
            "salary": "", "job_type": "", "deadline": "", "summary": "", "posted_date": "",
            "url": "https://jobs.code4lib.org/jobs/1-systems-librarian", "source": "Code4Lib Jobs"}
    base.update(overrides)
    return base


def test_details_reveal_employer_and_merge_cross_posting(tmp_path):
    conn = db.connect(tmp_path / "jobs.db")
    db.upsert(conn, _job(url="https://careers.insidehighered.com/job/5", source="IHE Library",
                         organization="New York Botanical Garden", location="Bronx, NY", state="NY"), NOW.isoformat())
    db.upsert(conn, _job(), NOW.isoformat())
    assert db.counts(conn)["total"] == 2
    code4lib_id = conn.execute("SELECT job_id FROM listings WHERE url LIKE '%code4lib%'").fetchone()[0]
    db.apply_details(conn, code4lib_id, {"organization": "New York Botanical Garden", "salary": "$77,000"})
    [merged] = db.active_jobs(conn, NOW)
    assert db.counts(conn)["total"] == 1
    assert {l["source"] for l in merged["listings"]} == {"IHE Library", "Code4Lib Jobs"}
    assert merged["salary"] == "$77,000"
