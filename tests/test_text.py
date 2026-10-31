import pytest

from aggregator.text import clean, dedupe_key, extract_salary, job_types, parse_location, remote_status


def test_clean_decodes_double_encoded_entities_and_tags():
    assert clean("Research &amp;amp; Instruction Librarian") == "Research & Instruction Librarian"
    assert clean("<p>Head of\n  <b>Archives</b></p>") == "Head of Archives"
    assert clean(None) == ""


@pytest.mark.parametrize("text, expected", [
    ("Salary Range: $69,000.00- $70,725.00", "$69,000.00- $70,725.00"),
    ("Pay: $20-$25/hour, part time", "$20-$25/hour"),
    ("$30.00/hour Job Opening URL:https://x", "$30.00/hour"),
    ("$52K – $60K annually", "$52K – $60K annually"),
    ("Application fee $5. Salary $48,500 per year", "$48,500 per year"),
    ("Salary Not specified", ""),
    ("Competitive", ""),
])
def test_extract_salary(text, expected):
    assert extract_salary(text) == expected


@pytest.mark.parametrize("raw, expected", [
    ("Ames, IA", ("Ames, IA", "IA")),
    ("Beaumont, TX, TX", ("Beaumont, TX", "TX")),
    ("Beaumont, Texas, United States", ("Beaumont, TX", "TX")),
    ("New York, United States", ("New York", "NY")),
    ("Washington, DC", ("Washington, DC", "DC")),
    ("Lebanon (LB)", ("Lebanon (LB)", "")),
    ("", ("", "")),
])
def test_parse_location(raw, expected):
    assert parse_location(raw) == expected


def test_job_types_from_title_and_schema():
    assert job_types("PT Records Center Assistant") == ["Part-time"]
    assert job_types("Part-time Instructor- IST564") == ["Part-time"]
    assert job_types("Library Assistant - Nights and Weekends, PART TIME") == ["Part-time"]
    assert job_types("Optimal Library Assistant") == []  # "pt" inside a word
    assert job_types("Paid Fall 2026 Processing Intern") == ["Internship"]
    assert job_types("Archivist (term)") == ["Temporary"]
    assert job_types("Project Archivist") == ["Temporary"]
    assert job_types("Project Manager, Digital Initiatives") == []
    assert job_types("Librarian", ["FULL_TIME"]) == ["Full-time"]
    assert job_types("Library Assistant", ["PART_TIME", "FULL_TIME"]) == ["Part-time"]


def test_remote_status():
    assert remote_status("Metadata Librarian (Remote)") == "remote"
    assert remote_status("Archivist", "Hybrid - Chicago, IL") == "hybrid"
    assert remote_status("Remote Sensing Data Analyst") == "remote"  # known false positive, acceptable
    assert remote_status("Reference Librarian", "Ames, IA") == ""


def test_dedupe_key_matches_across_boards():
    a = dedupe_key("Discovery Librarian - Librarian II #00089935", "Missouri University of Science and Technology", "u1")
    b = dedupe_key("Discovery Librarian – Librarian II", "Missouri University of Science & Technology", "u2")
    assert a == b


def test_dedupe_key_without_employer_uses_url():
    assert dedupe_key("Library Assistant", "", "https://a") != dedupe_key("Library Assistant", "", "https://b")
