import shutil
from pathlib import Path

import pytest

from medsched import Routine, build_plan, parse_label, to_html, to_ics
from medsched.cli import main

EX = Path(__file__).resolve().parent.parent / "examples"


def test_simple_once_daily():
    m = parse_label("Lisinopril 10 mg tablets\nTake 1 tablet by mouth once daily")
    assert (m.drug, m.strength, m.dose, m.times_per_day, m.route, m.decision) == \
        ("Lisinopril", "10mg", 1, 1, "by mouth", "schedule")


@pytest.mark.parametrize("sig,n", [
    ("Take 1 tablet twice daily", 2), ("Take 1 tablet three times a day", 3),
    ("Take 1 capsule every 8 hours", 3), ("Take 1 tablet every 12 hours", 2),
    ("Take 1 tablet every morning and at bedtime", 2), ("Take one-half tablet daily", 1),
])
def test_frequencies(sig, n):
    m = parse_label("Drugname 5 mg tablets\n" + sig)
    assert m.decision == "schedule", m.reasons
    assert m.times_per_day == n


def test_half_tablet():
    assert parse_label("Drugname 5 mg\nTake one-half tablet by mouth daily").dose == 0.5


@pytest.mark.parametrize("sig,needle", [
    ("Take 1 tablet QD", "QID"),
    ("Take 1 tablet QOD", "every other day"),
    ("Inject 10 U daily", "units"),
    ("Take 1.0 mg daily", "trailing zero"),
    ("Take .5 mg daily", "leading zero"),
    ("Take as directed", "as directed"),
    ("Take 1-2 tablets daily", "range"),
    ("Take 4 tablets daily for 3 days, then 2 tablets daily", "taper"),
    ("Inject per sliding scale", "measurement"),
    ("Take 1 tablet by mouth", "how often"),
    ("Take 6 tablets daily", "unusual"),
])
def test_abstains_instead_of_guessing(sig, needle):
    m = parse_label("Drugname 5 mg tablets\n" + sig)
    assert m.decision == "ask"
    assert any(needle.lower() in r.lower() for r in m.reasons), m.reasons


def test_prn_goes_to_as_needed_with_max():
    m = parse_label("Acetaminophen 500 mg\nTake 2 tablets every 6 hours as needed for pain. "
                    "Do not take more than 6 tablets in 24 hours.")
    assert m.decision == "as-needed" and m.prn_reason == "pain" and m.max_per_day == 6


def test_schedule_exceeding_label_maximum_is_flagged():
    m = parse_label("Ibuprofen 200 mg\nTake 2 tablets four times daily. Do not take more than 6 tablets per day.")
    assert m.decision == "ask" and any("maximum" in r for r in m.reasons)


def test_duplicate_drug_across_labels():
    meds = [parse_label("Lisinopril 10 mg\nTake 1 tablet daily"), parse_label("Lisinopril 20 mg\nTake 1 tablet daily")]
    plan = build_plan(meds)
    assert plan.warnings and len(plan.ask) == 2 and not plan.by_time


def test_low_ocr_confidence_abstains():
    m = parse_label("Amlodipine 5 mg\nTake 1 tablet daily", ocr_confidence=0.55)
    assert m.decision == "ask"


def test_plan_times_follow_routine():
    meds = [parse_label("Metformin 500 mg\nTake 1 tablet twice daily with meals"),
            parse_label("Atorvastatin 20 mg\nTake 1 tablet at bedtime")]
    plan = build_plan(meds, Routine(wake="07:00", dinner="18:30", bed="22:00"))
    assert list(plan.by_time) == ["07:00", "18:30", "22:00"]


def test_outputs(tmp_path):
    plan = build_plan([parse_label(b) for b in (EX / "labels.txt").read_text().split("\n\n") if b.strip()])
    ics = to_ics(plan)
    assert ics.startswith("BEGIN:VCALENDAR") and ics.count("BEGIN:VEVENT") == sum(map(len, plan.by_time.values()))
    page = to_html(plan)
    assert "Ask your pharmacist" in page and "not medical advice" in page
    assert main([str(EX / "labels.txt"), "--html", str(tmp_path / "s.html"), "--ics", str(tmp_path / "m.ics")]) == 0


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_ocr_sharp_vs_blurry():
    pytest.importorskip("pytesseract")
    from medsched.ocr import read_label
    sharp = parse_label(*read_label(str(EX / "label_sharp.png")))
    blurry = parse_label(*read_label(str(EX / "label_blurry.png")))
    assert sharp.decision == "schedule" and sharp.drug == "Amlodipine"
    assert blurry.decision == "ask"


def test_specific_weekdays():
    m = parse_label("Methotrexate 2.5 mg\nTake 4 tablets by mouth every Monday")
    assert m.decision == "schedule" and m.kind == "weekdays" and m.weekdays == [0]
    m = parse_label("Drugname 5 mg\nTake 1 tablet every Monday and Thursday at bedtime")
    assert m.weekdays == [0, 3] and m.time_hint == "bedtime"
    m = parse_label("Drugname 5 mg\nTake 1 tablet twice daily on Mondays")
    assert m.decision == "ask"


def test_midday_timing():
    m = parse_label("Drugname 5 mg\nTake 1 tablet daily with lunch")
    plan = build_plan([m], Routine(lunch="13:00"))
    assert list(plan.by_time) == ["13:00"]


def test_weekly_chart_and_ics_byday():
    from datetime import date

    from medsched import takes_on, to_weekly_html

    weekly = parse_label("Alendronate 70 mg\nTake 1 tablet once a week on Sunday morning")
    daily = parse_label("Lisinopril 10 mg\nTake 1 tablet daily")
    alt = parse_label("Furosemide 20 mg\nTake 1 tablet every other day")
    plan = build_plan([weekly, daily, alt])
    start = date(2026, 11, 2)  # a Monday
    days = [date(2026, 11, 2 + k) for k in range(7)]
    assert [takes_on(weekly, d, start) for d in days] == [False] * 6 + [True]
    assert [takes_on(alt, d, start) for d in days] == [True, False] * 3 + [True]
    page = to_weekly_html(plan, start)
    assert page.count("Lisinopril") >= 7 and page.count("Alendronate") == 2  # one cell + legend
    ics = to_ics(plan, start)
    assert "RRULE:FREQ=WEEKLY;BYDAY=SU" in ics and "DTSTART:20261108T" in ics
    assert "RRULE:FREQ=DAILY;INTERVAL=2" in ics


def test_cli_week(tmp_path):
    out = tmp_path / "week.html"
    assert main([str(EX / "labels.txt"), "--week", str(out), "--start", "2026-11-02"]) == 0
    assert "Weekly medicine chart" in out.read_text()
