"""Command line: `python -m aggregator run | build | import-legacy OLD_DB`."""
import argparse
import logging
import os
import sys
from pathlib import Path

from . import config, db, site, sources
from .details import DetailFetcher

log = logging.getLogger("aggregator")


class HealthCheckFailed(RuntimeError):
    pass


def check_health(results: dict, jobs: list):
    ok = [name for name, result in results.items() if isinstance(result, int) and result > 0]
    problems = []
    if len(ok) < config.MIN_SOURCES_OK:
        problems.append(f"only {len(ok)} of {len(results)} sources returned jobs (need {config.MIN_SOURCES_OK})")
    if len(jobs) < config.MIN_JOBS_FETCHED:
        problems.append(f"only {len(jobs)} jobs fetched (need {config.MIN_JOBS_FETCHED})")
    if problems:
        raise HealthCheckFailed("; ".join(problems))


def enrich(conn, limit):
    fetcher = DetailFetcher()
    pending = db.jobs_needing_details(conn, limit)
    done = failed = 0
    for job_id, url in pending:
        try:
            db.apply_details(conn, job_id, fetcher.fetch(url))
            done += 1
        except Exception as exc:
            log.warning("Detail fetch failed for %s: %s", url, exc)
            db.apply_details(conn, job_id, error=str(exc))
            failed += 1
        conn.commit()
    return done, failed


def write_summary(results, tallies, details, active):
    """Append a Markdown report to the GitHub Actions run page, when running there."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["## Job feed update", "", "| Source | Jobs |", "| --- | --- |"]
    lines += [f"| {name} | {result} |" for name, result in results.items()]
    lines += ["", f"New: {tallies['new']} · Also listed elsewhere: {tallies['merged']} · "
                  f"Seen again: {tallies['seen']} · Expired: {tallies['expired']}",
              f"Detail pages read: {details[0]} ok, {details[1]} failed",
              f"**Active jobs published: {active}**", ""]
    with open(path, "a") as handle:
        handle.write("\n".join(lines))


def run(args):
    jobs, results = sources.fetch_all()
    log.info("Fetched %d jobs from %d sources", len(jobs), len(results))
    if os.environ.get("GITHUB_ACTIONS"):
        # Shows each broken source on the run page even when the run as a whole succeeds.
        for name, result in results.items():
            if str(result).startswith("error"):
                print(f"::warning title={name} failed::{result}")
    if not args.skip_health_check:
        check_health(results, jobs)

    conn = db.connect(args.db)
    tallies = {"new": 0, "merged": 0, "seen": 0}
    seen_at = db.now_iso()
    for job in jobs:
        tallies[db.upsert(conn, job, seen_at)] += 1
    conn.commit()
    log.info("New: %(new)d, merged with another board: %(merged)d, seen again: %(seen)d", tallies)

    details = (0, 0) if args.no_details else enrich(conn, args.detail_limit)
    log.info("Detail pages: %d read, %d failed", *details)

    tallies["expired"] = db.expire(conn)
    conn.commit()
    log.info("Expired %d jobs", tallies["expired"])

    active = site.build(conn, args.out)
    write_summary(results, tallies, details, active)
    conn.execute("VACUUM")
    conn.close()


def build(args):
    conn = db.connect(args.db)
    site.build(conn, args.out)
    conn.close()


def import_legacy(args):
    conn = db.connect(args.db)
    count = db.import_legacy(conn, args.legacy_db)
    expired = db.expire(conn)
    conn.commit()
    conn.execute("VACUUM")
    log.info("Imported %d jobs from %s (%d already expired); totals %s",
             count, args.legacy_db, expired, db.counts(conn))
    conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m aggregator", description=__doc__)
    parser.add_argument("--db", type=Path, default=config.DB_PATH, help="SQLite database path")
    parser.add_argument("--out", type=Path, default=config.SITE_DIR, help="site output directory")
    parser.add_argument("-v", "--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    run_cmd = commands.add_parser("run", help="fetch feeds, update the database, build the site")
    run_cmd.add_argument("--no-details", action="store_true", help="skip reading job detail pages")
    run_cmd.add_argument("--detail-limit", type=int, default=config.DETAIL_LIMIT_PER_RUN)
    run_cmd.add_argument("--skip-health-check", action="store_true",
                         help="publish even if most sources failed")
    run_cmd.set_defaults(func=run)

    commands.add_parser("build", help="rebuild the site from the database only").set_defaults(func=build)

    legacy_cmd = commands.add_parser("import-legacy", help="import the old Raspberry Pi jobs.db")
    legacy_cmd.add_argument("legacy_db", type=Path)
    legacy_cmd.set_defaults(func=import_legacy)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    try:
        args.func(args)
    except HealthCheckFailed as exc:
        log.error("Health check failed, not publishing: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
