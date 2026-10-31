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
    assert stats["open_by_state"] == [{"location": "Pennsylvania", "jobs": 1},
                                      {"location": trends.OTHER_LOCATION, "jobs": 1}]
    assert {"type": "Hybrid", "jobs": 1} in stats["open_by_type"]
    assert sum(b["jobs"] for b in stats["yearly_pay_distribution"]) == 1


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
