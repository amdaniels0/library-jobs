"""SQLite storage. One row per job in `jobs`; one row per URL it was listed at in `listings`."""
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import config
from .text import canonical_url, clean, dedupe_key, parse_location

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    organization TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    remote TEXT NOT NULL DEFAULT '',
    salary TEXT NOT NULL DEFAULT '',
    job_type TEXT NOT NULL DEFAULT '',
    deadline TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    posted_date TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    detail_status TEXT NOT NULL DEFAULT 'pending',
    detail_attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(active);
CREATE TABLE IF NOT EXISTS listings (
    url TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listings_job ON listings(job_id);
"""

# Fields a later sighting may fill in when they were blank before.
FILLABLE = ("organization", "location", "state", "remote", "salary", "job_type", "deadline", "summary", "posted_date")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path=config.DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _fill_blanks(conn, job_id, job):
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    updates = {f: job[f] for f in FILLABLE if job.get(f) and not row[f]}
    if updates:
        assignments = ", ".join(f"{f} = ?" for f in updates)
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*updates.values(), job_id))


def upsert(conn, job, seen_at=None) -> str:
    """Record one sighting of a job. Returns 'seen', 'merged' (new board for a known job) or 'new'."""
    seen_at = seen_at or now_iso()
    listing = conn.execute("SELECT job_id FROM listings WHERE url = ?", (job["url"],)).fetchone()
    if listing:
        job_id = listing["job_id"]
        conn.execute("UPDATE listings SET last_seen = ? WHERE url = ?", (seen_at, job["url"]))
        conn.execute("UPDATE jobs SET last_seen = ?, active = 1 WHERE id = ?", (seen_at, job_id))
        _fill_blanks(conn, job_id, job)
        return "seen"

    key = dedupe_key(job["title"], job["organization"], job["url"])
    find = "SELECT id, location, active FROM jobs WHERE dedupe_key = ?"
    existing = conn.execute(find, (key,)).fetchone()
    if existing and _same_board_elsewhere(conn, existing, job):
        # Same title and employer on the same board but another location is a separate opening
        # (e.g. one per campus), not a cross-posting.
        key = f'{key}|{job["location"].lower()}'
        existing = conn.execute(find, (key,)).fetchone()
    if existing and not existing["active"]:
        # An expired job posted again (often a yearly repost): treat it as a fresh posting.
        job_id = existing["id"]
        conn.execute(
            f"""UPDATE jobs SET title = ?, {", ".join(f"{f} = ?" for f in FILLABLE)}, url = ?, source = ?,
                   first_seen = ?, last_seen = ?, active = 1, detail_status = 'pending', detail_attempts = 0
                WHERE id = ?""",
            (job["title"], *(job[f] for f in FILLABLE), job["url"], job["source"], seen_at, seen_at, job_id))
        status = "new"
    elif existing:
        job_id = existing["id"]
        conn.execute("UPDATE jobs SET last_seen = ?, active = 1 WHERE id = ?", (seen_at, job_id))
        _fill_blanks(conn, job_id, job)
        status = "merged"
    else:
        cursor = conn.execute(
            """INSERT INTO jobs (dedupe_key, title, organization, location, state, remote, salary,
                   job_type, deadline, summary, url, source, posted_date, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, job["title"], job["organization"], job["location"], job["state"], job["remote"],
             job["salary"], job["job_type"], job["deadline"], job["summary"], job["url"],
             job["source"], job["posted_date"], seen_at, seen_at))
        job_id = cursor.lastrowid
        status = "new"
    conn.execute("INSERT INTO listings (url, job_id, source, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
                 (job["url"], job_id, job["source"], seen_at, seen_at))
    return status


def _same_board_elsewhere(conn, existing, job) -> bool:
    if not job["location"] or existing["location"].lower() == job["location"].lower():
        return False
    board = config.BOARD_BY_SOURCE.get(job["source"], job["source"])
    sources = [row["source"] for row in conn.execute("SELECT source FROM listings WHERE job_id = ?", (existing["id"],))]
    return any(config.BOARD_BY_SOURCE.get(s, s) == board for s in sources)


def jobs_needing_details(conn, limit=config.DETAIL_LIMIT_PER_RUN) -> list:
    """Active jobs with a listing on a board whose pages carry JobPosting data, not yet fetched."""
    rows = conn.execute(
        """SELECT j.id, l.url FROM jobs j JOIN listings l ON l.job_id = j.id
           WHERE j.active = 1 AND j.detail_status IN ('pending', 'failed')
             AND j.detail_attempts < ?
           ORDER BY j.organization != '', j.detail_status = 'failed', j.first_seen DESC""",
        (config.DETAIL_MAX_ATTEMPTS,)).fetchall()
    picked, seen = [], set()
    for row in rows:
        if row["id"] in seen or urlparse(row["url"]).netloc not in config.DETAIL_DOMAINS:
            continue
        seen.add(row["id"])
        picked.append((row["id"], row["url"]))
        if len(picked) >= limit:
            break
    return picked


def apply_details(conn, job_id, fields=None, error=None):
    if error:
        conn.execute("UPDATE jobs SET detail_status = 'failed', detail_attempts = detail_attempts + 1 WHERE id = ?",
                     (job_id,))
        return
    # Structured page data is more reliable than what we parsed out of feed text.
    allowed = ("location", "state", "remote", "salary", "job_type", "deadline", "posted_date", "organization")
    updates = {k: v for k, v in (fields or {}).items() if k in allowed and v}
    updates["detail_status"] = "done"
    assignments = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f"UPDATE jobs SET {assignments}, detail_attempts = detail_attempts + 1 WHERE id = ?",
                 (*updates.values(), job_id))
    _rekey(conn, job_id)


def _rekey(conn, job_id):
    """Recompute the dedupe key after page data filled in the employer (Code4Lib, CLIR+DLF feeds omit it).

    If the job turns out to be listed on another board already, fold it into that job.
    """
    job = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    key = dedupe_key(job["title"], job["organization"], job["url"])
    if key == job["dedupe_key"]:
        return
    other = conn.execute("SELECT id, location, active FROM jobs WHERE dedupe_key = ?", (key,)).fetchone()
    if other and _same_board_elsewhere(conn, other, job):
        key = f'{key}|{job["location"].lower()}'
        other = conn.execute("SELECT id, location, active FROM jobs WHERE dedupe_key = ?", (key,)).fetchone()
    if other and not other["active"]:
        # An expired job (e.g. last year's posting) keeps its history under a retired key.
        conn.execute("UPDATE jobs SET dedupe_key = dedupe_key || '|retired-' || id WHERE id = ?", (other["id"],))
        other = None
    if not other:
        conn.execute("UPDATE jobs SET dedupe_key = ? WHERE id = ?", (key, job_id))
        return
    conn.execute("UPDATE listings SET job_id = ? WHERE job_id = ?", (other["id"], job_id))
    conn.execute("UPDATE jobs SET last_seen = MAX(last_seen, ?), active = 1 WHERE id = ?",
                 (job["last_seen"], other["id"]))
    _fill_blanks(conn, other["id"], job)
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def expire(conn, now=None) -> int:
    """Deactivate jobs no feed has listed for EXPIRY_DAYS, or whose deadline has passed."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=config.EXPIRY_DAYS)).isoformat()
    today = now.date().isoformat()
    cursor = conn.execute(
        """UPDATE jobs SET active = 0
           WHERE active = 1 AND (last_seen < ? OR (deadline != '' AND deadline < ?))""",
        (cutoff, today))
    return cursor.rowcount


def active_jobs(conn, now=None) -> list:
    jobs = [dict(row) for row in conn.execute(
        """SELECT * FROM jobs WHERE active = 1
           ORDER BY COALESCE(NULLIF(posted_date, ''), first_seen) DESC, id DESC""")]
    # Listings no board has shown lately (e.g. last year's posting of a reposted job) are history, not links.
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=config.EXPIRY_DAYS)).isoformat()
    sources = {}
    for row in conn.execute(
            "SELECT l.job_id, l.source, l.url FROM listings l JOIN jobs j ON j.id = l.job_id "
            "WHERE j.active = 1 AND l.last_seen >= ? ORDER BY l.first_seen", (cutoff,)):
        sources.setdefault(row["job_id"], []).append({"source": row["source"], "url": row["url"]})
    for job in jobs:
        job["listings"] = sources.get(job["id"], [{"source": job["source"], "url": job["url"]}])
    return jobs


def counts(conn) -> dict:
    total, active = conn.execute("SELECT COUNT(*), COALESCE(SUM(active), 0) FROM jobs").fetchone()
    return {"total": total, "active": active}


def import_legacy(conn, legacy_path) -> int:
    """Import the Raspberry Pi database (old `jobs` table with job_hash/source_feed columns)."""
    legacy = sqlite3.connect(f"file:{legacy_path}?mode=ro", uri=True)
    legacy.row_factory = sqlite3.Row
    imported = 0
    for row in legacy.execute("SELECT * FROM jobs ORDER BY fetched_date"):
        title, organization = clean(row["title"]), clean(row["organization"])
        if organization and title.lower().startswith(organization.lower() + ":"):
            title = title[len(organization) + 1:].strip()
        location, state = parse_location(row["location"])
        job = {
            "title": title, "organization": organization, "location": location, "state": state,
            "remote": "", "salary": clean(row["salary"]), "job_type": "", "deadline": "",
            "summary": "", "url": canonical_url(row["url"]), "source": row["source_feed"],
            "posted_date": row["posted_date"] or "",
        }
        first_seen = _legacy_time(row["fetched_date"])
        if upsert(conn, job, seen_at=first_seen) == "new":
            imported += 1
        last_seen = _legacy_time(row["last_seen_date"])
        conn.execute("UPDATE listings SET first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?) WHERE url = ?",
                     (first_seen, last_seen, job["url"]))
        conn.execute("UPDATE jobs SET last_seen = MAX(last_seen, ?), first_seen = MIN(first_seen, ?) "
                     "WHERE id = (SELECT job_id FROM listings WHERE url = ?)", (last_seen, first_seen, job["url"]))
    legacy.close()
    # Every legacy job counts as already enriched-or-not; don't hit old URLs again.
    conn.execute("UPDATE jobs SET detail_status = 'done'")
    conn.commit()
    return imported


def _legacy_time(value) -> str:
    """Pi timestamps were naive US Eastern; store as UTC ISO like everything else."""
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo(config.TIMEZONE))
    return stamp.astimezone(timezone.utc).replace(microsecond=0).isoformat()
