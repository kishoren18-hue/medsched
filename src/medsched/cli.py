"""medsched labels.txt [--html schedule.html] [--ics meds.ics] [--image label.jpg ...]

labels.txt holds one medication per block, separated by blank lines:

    Lisinopril 10 mg tablets
    Take 1 tablet by mouth once daily

    Metformin 500 mg tablets
    Take 1 tablet by mouth twice daily with meals
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .parse import parse_label
from .schedule import Routine, build_plan, label, to_html, to_ics, to_weekly_html


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="medsched", description="Organize pharmacy label directions into a daily "
                                 "schedule, and refuse to guess when a label is unclear. Not medical advice.")
    ap.add_argument("labels", nargs="?", help="text file, one label per blank-line-separated block")
    ap.add_argument("--image", nargs="*", default=[], help="label photos to OCR (needs medsched[ocr])")
    ap.add_argument("--html", help="write a printable schedule here")
    ap.add_argument("--ics", help="write a calendar file here")
    ap.add_argument("--week", help="write a printable 7-day chart here (for caregivers and pill organizers)")
    ap.add_argument("--start", help="first day for the calendar and weekly chart, YYYY-MM-DD (default today)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--wake", default="08:00")
    ap.add_argument("--lunch", default="12:30")
    ap.add_argument("--dinner", default="18:00")
    ap.add_argument("--bed", default="21:30")
    a = ap.parse_args(argv)

    meds = []
    if a.labels:
        for block in Path(a.labels).read_text().split("\n\n"):
            if block.strip():
                meds.append(parse_label(block))
    if a.image:
        from .ocr import read_label
        for img in a.image:
            text, conf = read_label(img)
            meds.append(parse_label(text, ocr_confidence=conf))
    if not meds:
        ap.error("give a labels file and/or --image")

    plan = build_plan(meds, Routine(a.wake, a.lunch, a.dinner, a.bed))
    if a.json:
        print(json.dumps({"schedule": {t: [x for _, x in items] for t, items in plan.by_time.items()},
                          "as_needed": [label(m) for m in plan.as_needed],
                          "ask_pharmacist": [{"label": m.raw, "reasons": m.reasons} for m in plan.ask],
                          "warnings": plan.warnings}, indent=2))
    else:
        for w in plan.warnings:
            print(f"! {w}")
        for t, items in plan.by_time.items():
            for _, text in items:
                print(f"{t}  {text}")
        for m in plan.as_needed:
            print(f"as needed  {label(m)}" + (f" (for {m.prn_reason})" if m.prn_reason else ""))
        for m in plan.ask:
            print(f"ASK PHARMACIST  {m.drug or m.raw[:40]}: {'; '.join(m.reasons)}")
    if a.html:
        Path(a.html).write_text(to_html(plan))
    start = date.fromisoformat(a.start) if a.start else None
    if a.ics:
        Path(a.ics).write_text(to_ics(plan, start))
    if a.week:
        Path(a.week).write_text(to_weekly_html(plan, start))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
