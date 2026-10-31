import json
from datetime import datetime, timedelta, timezone

import feedparser
import pytest

from aggregator import db, site

NOW = datetime(2026, 10, 31, 14, 0, tzinfo=timezone.utc)


def job(**overrides):
    base = {
        "title": "Digital Archivist", "organization": "Example University", "location": "Pittsburgh, PA",
        "state": "PA", "remote": "", "salary": "", "job_type": "", "deadline": "", "summary": "",
        "url": "https://www.higheredjobs.com/details.cfm?JobCode=1", "source": "HigherEdJobs Libraries",
        "posted_date": "2026-10-30T12:00:00+00:00",
    }
    base.update(overrides)
    return base


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    yield connection
    connection.close()


def test_upsert_seen_merged_new(conn):
    assert db.upsert(conn, job(), NOW.isoformat()) == "new"
    assert db.upsert(conn, job(), NOW.isoformat()) == "seen"
    other_board = job(url="https://careers.insidehighered.com/job/9", source="IHE Archives",
                      salary="$60,000", deadline="2026-12-01")
    assert db.upsert(conn, other_board, NOW.isoformat()) == "merged"
    assert db.upsert(conn, job(title="Records Manager", url="https://x/2"), NOW.isoformat()) == "new"

    jobs = {j["title"]: j for j in db.active_jobs(conn)}
    archivist = jobs["Digital Archivist"]
    assert len(archivist["listings"]) == 2
    assert archivist["salary"] == "$60,000"  # blank filled from the second board
    assert db.counts(conn) == {"total": 2, "active": 2}


def test_same_board_other_location_is_a_separate_opening(conn):
    db.upsert(conn, job(location="Houston, TX", state="TX"), NOW.isoformat())
    assert db.upsert(conn, job(url="https://x/2", location="Kingwood, TX", state="TX"), NOW.isoformat()) == "new"
    assert db.upsert(conn, job(url="https://x/3", location="Houston, TX", state="TX"), NOW.isoformat()) == "merged"
    assert db.upsert(conn, job(url="https://x/4", location="Kingwood, TX", state="TX"), NOW.isoformat()) == "merged"
    # A different board with a differently written location is still a cross-posting.
    assert db.upsert(conn, job(url="https://careers.insidehighered.com/job/9", source="IHE Archives",
                               location="Houston"), NOW.isoformat()) == "merged"
    assert db.counts(conn)["total"] == 2


def test_repost_of_expired_job_is_fresh(conn):
    last_year = NOW - timedelta(days=360)
    db.upsert(conn, job(posted_date=last_year.isoformat(), salary="$40,000"), last_year.isoformat())
    assert db.expire(conn, NOW) == 1
    assert db.upsert(conn, job(url="https://x/repost", posted_date=NOW.isoformat()), NOW.isoformat()) == "new"
    [fresh] = db.active_jobs(conn, NOW)
    assert fresh["first_seen"] == NOW.isoformat()
    assert fresh["posted_date"] == NOW.isoformat()
    assert fresh["url"] == "https://x/repost"
    assert fresh["salary"] == ""  # last year's details don't carry over
    assert [l["url"] for l in fresh["listings"]] == ["https://x/repost"]  # last year's link isn't offered
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 2  # but history is kept


def test_detail_queue_only_uses_jsonld_boards(conn):
    db.upsert(conn, job(), NOW.isoformat())
    assert db.jobs_needing_details(conn) == []
    db.upsert(conn, job(url="https://careers.insidehighered.com/job/9", source="IHE Archives"), NOW.isoformat())
    [(job_id, url)] = db.jobs_needing_details(conn)
    assert url.startswith("https://careers.insidehighered.com/")
    db.apply_details(conn, job_id, {"deadline": "2026-12-01", "location": "Pittsburgh, PA", "bogus": "x"})
    assert db.jobs_needing_details(conn) == []


def test_expire_by_last_seen_and_deadline(conn):
    db.upsert(conn, job(url="https://x/old"), (NOW - timedelta(days=61)).isoformat())
    db.upsert(conn, job(title="Past deadline", url="https://x/due", deadline="2026-10-30"), NOW.isoformat())
    db.upsert(conn, job(title="Current", url="https://x/ok", deadline="2026-11-30"), NOW.isoformat())
    assert db.expire(conn, NOW) == 2
    assert [j["title"] for j in db.active_jobs(conn)] == ["Current"]


def test_site_escapes_and_trims_published_fields(conn, tmp_path):
    db.upsert(conn, job(title='<script>alert("x")</script> Archivist', url="https://x/1"), NOW.isoformat())
    db.upsert(conn, job(title="Bad link", url="javascript:alert(1)", organization="Other"), NOW.isoformat())
    conn.execute("UPDATE jobs SET deadline = 'rolling'")  # malformed data must not break the build
    out = tmp_path / "site"
    assert site.build(conn, out, now=NOW) == 1

    page = (out / "index.html").read_text()
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; Archivist" in page
    assert "javascript:" not in page
    assert "{{" not in page

    data = json.loads((out / "jobs.json").read_text())
    assert data["total_jobs"] == 1
    assert "contact_email" not in data["jobs"][0] and "full_description" not in data["jobs"][0]

    feed = feedparser.parse((out / "consolidated-jobs.xml").read_bytes())
    assert not feed.bozo and len(feed.entries) == 1
    assert feed.entries[0].title.startswith('<script>alert("x")</script> Archivist')
