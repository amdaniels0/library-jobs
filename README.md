# Library & Archives Jobs

A consolidated list of library, archives, museum, and information science jobs, gathered from several job boards twice a day.

- **[Browse jobs](https://amdaniels0.github.io/library-jobs/)**: search and filter by board, state, job type, posting date, and remote work. Filters live in the URL, so a filtered view can be bookmarked or shared.
- **[RSS feed](https://amdaniels0.github.io/library-jobs/consolidated-jobs.xml)**: the 500 most recently found jobs.
- **[JSON](https://amdaniels0.github.io/library-jobs/jobs.json)**: every open job, for programmatic use.

## Sources

| Board | Feeds | What it provides |
| --- | --- | --- |
| [HigherEdJobs](https://www.higheredjobs.com/) | Libraries; Library & Information Science; Arts & Museum Administration; Data Science | Title, employer, location |
| [Chronicle of Higher Education](https://jobs.chronicle.com/) | Library & Library Administration category, plus keyword searches: archivist, curator, museum, digital preservation, metadata | Plus deadline and job type from each job page |
| [Inside Higher Ed](https://careers.insidehighered.com/) | Keyword searches: libraries, library, archives, information science, archivist, curator, museum, digital preservation, metadata | Plus deadline and job type from each job page |
| [Archives Gig](https://archivesgig.com/) | All posts | Salary and deadline from the post's labelled fields |
| [SAA Careers](https://careers.archivists.org/) | All jobs | Title, employer, location, salary when mentioned |
| [Simmons SLIS Jobline](https://slis-jobline.simmons.edu/) | All posts (three feed pages) | Strongest source for public libraries; salary in most posts |
| [CLIR+DLF Job Board](https://jobs.diglib.org/) | Active jobs | Employer, location, salary range, and deadline from each job page (the board requires a salary) |
| [Code4Lib Jobs](https://jobs.code4lib.org/) | All jobs | Library technology roles; employer, location, and salary from each job page |
| [Digital Preservation Coalition](https://www.dpconline.org/news/job-vacancies) | Vacancies | Digital preservation roles, mostly outside the US |
| [USAJOBS](https://www.usajobs.gov/) | Federal series: librarian, library and archives technicians, archivist, museum curator and specialist, exhibits, records management | Salary, deadline, location, schedule. **Optional: needs an API key (see below)** |

Keyword searches match anywhere in a posting, so their results pass through a job-title filter (`relevant_title` in `aggregator/text.py`) that keeps library, archives, museum, and related titles.

Checked and not used (October 2026): ALA JobLIST, AALL, AASLH, NCPH, and EDUCAUSE block automated requests; AAM JobHQ and the SLA Career Center have no feed; MLA's feed requires a login; AMIA's feed returns errors; H-Net's feed is empty; the Museum Employment Resource Center's feed stopped in 2017.

### Turning on USAJOBS

1. Request a free API key at <https://developer.usajobs.gov/apirequest/>.
2. In the repository on GitHub: Settings → Secrets and variables → Actions → New repository secret. Add `USAJOBS_API_KEY` (the key) and `USAJOBS_EMAIL` (the email address the key was issued to).
3. Run the workflow ("Update job feed" → "Run workflow"). The run summary shows how many federal jobs came in. Until the secrets exist, USAJOBS is listed as skipped and everything else runs normally.

## How it works

A GitHub Actions workflow ([`.github/workflows/update.yml`](.github/workflows/update.yml)) runs at 10:00 and 22:00 UTC:

1. Restores the SQLite database (`jobs.db`) from the `data` branch.
2. `python -m aggregator run`:
   - fetches every feed and parses it with a board-specific parser ([`aggregator/sources.py`](aggregator/sources.py));
   - **health check**: stops without saving or publishing if fewer than 10 sources respond or fewer than 200 jobs come back, so an outage never wipes the site. A single failing source shows up as a warning on the run page;
   - merges each listing into the database ([`aggregator/db.py`](aggregator/db.py)). The same job posted on two boards becomes one entry with links to both. The same title on the same board in a different location stays a separate job, and a yearly repost of an expired job counts as new;
   - reads up to 80 job pages on Chronicle, Inside Higher Ed, CLIR+DLF, and Code4Lib for their schema.org `JobPosting` data (deadline, job type, employer, exact location, salary). Each page is fetched once, 3 seconds apart, and `robots.txt` is respected ([`aggregator/details.py`](aggregator/details.py)). When a page reveals an employer the feed left out, the job is matched against other boards again;
   - drops jobs whose deadline has passed or that no board has listed for 60 days;
   - writes `index.html`, `jobs.json`, and `consolidated-jobs.xml` to `_site/` ([`aggregator/site.py`](aggregator/site.py)).
3. Force-pushes the updated database to the `data` branch as a single commit, so the repository does not grow. Each run's database is also kept as a downloadable artifact for 30 days.
4. Deploys `_site/` to GitHub Pages. Generated files are never committed to `main`.

Each run's page in the Actions tab shows a per-source summary table.

## Running locally

```bash
uv venv --python 3.12 .venv   # or: python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m aggregator run --no-details   # feeds only; omit the flag to also read job pages
.venv/bin/python -m http.server 8765 --directory _site
```

To work from the live data, download `jobs.db` from the `data` branch into `data/jobs.db` first:

```bash
mkdir -p data && git fetch origin data && git show FETCH_HEAD:jobs.db > data/jobs.db
```

Other commands: `python -m aggregator build` rebuilds the site from the database without fetching; `python -m aggregator import-legacy OLD.db` imports the database from the original Raspberry Pi version, which holds the job history from Nov 2025 to Jun 2026.

## Maintenance

- **A run fails.** GitHub emails the repository owner. The run's log and summary show which sources failed. A board that changed its feed format usually needs a fix in its parser in `aggregator/sources.py`, with a test in `tests/test_sources.py`.
- **Adding a source.** Add an entry to `SOURCES` in [`aggregator/config.py`](aggregator/config.py). If its feed format differs from the existing ones, add a parser.
- **Scheduled runs stop.** GitHub disables scheduled workflows in public repositories after 60 days with no repository activity. Re-enable it from the Actions tab: "Update job feed", then "Enable workflow".
- **Restoring the database.** Download a `jobs-db` artifact from a recent run and push it to the `data` branch.

Job listings belong to their respective sources. This site only links to them.
