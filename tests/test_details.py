import json

from aggregator.details import parse_jobposting


def page(posting):
    return f'<html><head><script type="application/ld+json">{json.dumps(posting)}</script></head></html>'


def test_parses_madgex_jobposting():
    fields = parse_jobposting(page({
        "@context": "https://schema.org", "@type": "JobPosting",
        "datePosted": "2026-09-11T04:00:00.000Z", "validThrough": "2026-11-10T05:00:00.000Z",
        "employmentType": '["FULL_TIME"]',
        "hiringOrganization": {"@type": "Organization", "name": "Lamar University"},
        "jobLocation": [{"@type": "Place", "address": {"addressLocality": "Beaumont", "addressRegion": "Texas",
                                                       "addressCountry": "US"}}],
    }))
    assert fields == {
        "deadline": "2026-11-10", "posted_date": "2026-09-11T04:00:00.000Z", "job_type": "Full-time",
        "organization": "Lamar University", "location": "Beaumont, TX", "state": "TX",
    }


def test_salary_and_remote():
    fields = parse_jobposting(page({
        "@type": "JobPosting", "jobLocationType": "TELECOMMUTE",
        "baseSalary": {"@type": "MonetaryAmount", "currency": "USD",
                       "value": {"minValue": 55000, "maxValue": 65000, "unitText": "YEAR"}},
    }))
    assert fields["salary"] == "$55,000 - $65,000/year"
    assert fields["remote"] == "remote"


def test_no_jobposting():
    assert parse_jobposting("<html><script type='application/ld+json'>{bad json</script></html>") == {}
