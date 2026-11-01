import json
from datetime import datetime, timezone

import pytest

from aggregator import config, db, site, trends
from aggregator.trends import pay_value

NOW = datetime(2026, 10, 31, 14, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("salary, expected", [
    ("$52,000 - $60,000/year", ("year", 56000.0)),
    ("$70,623 - $91,815/year", ("year", 81219.0)),
    ("$20-$25/hour", ("hour", 22.5)),
    ("$30.00/hour", ("hour", 30.0)),
    ("$52K – $60K annually", ("year", 56000.0)),
    ("$62,500 – $78,000 per year", ("year", 70250.0)),
    ("$77,000", ("year", 77000.0)),
    ("$23.33", ("hour", 23.33)),
    ("$2,000", None),              # a stipend: neither plausible yearly nor hourly
    ("$4,500/month", None),
    ("", None),
])
def test_pay_value(salary, expected):
    assert pay_value(salary) == expected


def job(**overrides):
    base = {"title": "Archivist", "organization": "Example University", "location": "Pittsburgh, PA",
            "state": "PA", "remote": "", "salary": "", "job_type": "", "deadline": "", "summary": "",
            "url": "https://x/1", "source": "HigherEdJobs Libraries", "posted_date": ""}
    base.update(overrides)
    return base


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.upsert(connection, job(url="https://x/old", title="Old"), "2026-05-03T12:00:00+00:00")
    db.upsert(connection, job(url="https://x/1", salary="$60,000/year", job_type="Part-time"), NOW.isoformat())
    db.upsert(connection, job(url="https://x/2", title="Curator", organization="Museum", state="", location="",
                              salary="$25/hour", remote="hybrid"), NOW.isoformat())
    db.expire(connection, NOW)
    yield connection
    connection.close()


def test_compute(conn):
    stats = trends.compute(conn, db.active_jobs(conn, NOW), NOW)
    assert stats["open_jobs"] == 2
    months = {m["month"]: m for m in stats["new_per_month"]}
    assert list(months) == ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert months["2026-05"]["note"].startswith("Tracking began")
    assert months["2026-07"]["new_jobs"] == 0 and months["2026-07"]["note"].startswith("No data")
    assert months["2026-10"]["note"].startswith("Collection resumed")
    assert config.TRENDS_NOTES["2026-10"] in months["2026-10"]["note"]
    assert stats["median_yearly_pay"] == 60000 and stats["median_hourly_pay"] == 25
    assert stats["open_by_location"] == [
        {"location": "Pennsylvania", "jobs": 1}, {"location": "US, state not listed", "jobs": 0},
        {"location": "Outside the US", "jobs": 0}, {"location": "Location not listed", "jobs": 1}]
    assert {"type": "Hybrid", "jobs": 1} in stats["open_by_type"]
    assert sum(b["jobs"] for b in stats["yearly_pay_distribution"]) == 1


def test_locations_families_deadlines(tmp_path):
    conn = db.connect(tmp_path / "jobs.db")
    jobs = [
        job(url="https://x/a", title="University Archivist", location="Hong Kong, Hong Kong", state=""),
        job(url="https://x/b", title="Curator - NYUAD Art Gallery", location="AE", state="",
            source="Chronicle Curator", deadline="2026-11-05"),
        job(url="https://x/c", title="Systems Librarian", location="Georgia Gwinnett College", state="",
            source="IHE Library", deadline="2026-11-20"),
        job(url="https://x/d", title="Library Assistant", location="", state="", source="Code4Lib Jobs"),
        job(url="https://x/e", title="Dean of Libraries", deadline="2027-03-01"),
    ]
    for j in jobs:
        db.upsert(conn, j, NOW.isoformat())
    stats = trends.compute(conn, db.active_jobs(conn, NOW), NOW)
    groups = {row["location"]: row["jobs"] for row in stats["open_by_location"]}
    assert groups["Pennsylvania"] == 1 and groups["US, state not listed"] == 1
    assert groups["Outside the US"] == 2 and groups["Location not listed"] == 1
    assert {c["country"] for c in stats["open_outside_us_by_country"]} == {"Hong Kong", "United Arab Emirates"}
    families = {row["family"]: row["jobs"] for row in stats["open_by_family"]}
    assert families == {"Archives & special collections": 1, "Museums & curatorial": 1,
                        "Digital, data & technology": 1, "Library staff & access services": 1, "Leadership": 1}
    weeks = {w["week_of"]: w["jobs"] for w in stats["deadlines_by_week"]}
    assert weeks["2026-11-02"] == 1 and weeks["2026-11-16"] == 1 and sum(weeks.values()) == 2
    assert stats["closing_next_two_weeks"] == 1


def test_durations_skip_jobs_at_collection_gaps(tmp_path):
    conn = db.connect(tmp_path / "jobs.db")
    seen = lambda day: f"2026-{day}T12:00:00+00:00"  # noqa: E731
    # Collector ran Jan 1-31 (daily), then was off until Mar 1.
    for d in range(1, 32):
        db.upsert(conn, job(url=f"https://x/run{d}", title=f"Run marker {d}", organization=""), seen(f"01-{d:02d}"))
    db.upsert(conn, job(url="https://x/open-at-start", title="A"), seen("01-01"))   # already open: skipped
    db.upsert(conn, job(url="https://x/full", title="B"), seen("01-05"))
    conn.execute("UPDATE listings SET last_seen = ? WHERE url = 'https://x/full'", (seen("01-19"),))
    conn.execute("UPDATE jobs SET last_seen = ? WHERE url = 'https://x/full'", (seen("01-19"),))
    db.upsert(conn, job(url="https://x/cut-off", title="C"), seen("01-20"))
    conn.execute("UPDATE jobs SET last_seen = ? WHERE url = 'https://x/cut-off'", (seen("01-31"),))  # gap after
    db.upsert(conn, job(url="https://x/now", title="D"), seen("03-01"))
    # Same life as B, but on a board whose feed shows only its newest items: not counted.
    db.upsert(conn, job(url="https://x/gig", title="E", source="Archivesgig"), seen("01-05"))
    conn.execute("UPDATE jobs SET last_seen = ? WHERE url = 'https://x/gig'", (seen("01-19"),))
    conn.execute("UPDATE jobs SET active = 0 WHERE title IN ('A', 'B', 'C', 'E')")  # run markers stay open
    assert trends._durations(conn) == [14.0]


def test_trends_page_is_built_and_escaped(conn, tmp_path):
    db.upsert(conn, job(url="https://x/3", organization='<img src=x onerror="alert(1)">'), NOW.isoformat())
    out = tmp_path / "site"
    site.build(conn, out, now=NOW)
    page = (out / "trends.html").read_text()
    assert "{{" not in page
    assert "<img src=x" not in page and "&lt;img src=x" in page
    assert "No data" in page and "Show as table" in page
    assert json.loads((out / "trends.json").read_text())["open_jobs"] == 3
    assert (out / "trends.js").exists()
    assert 'href="trends.html"' in (out / "index.html").read_text()
