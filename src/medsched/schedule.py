"""Turn parsed medications into a daily plan, a printable page and a calendar file."""

from __future__ import annotations

import html
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .parse import Med


@dataclass
class Routine:
    wake: str = "08:00"
    lunch: str = "12:30"
    dinner: str = "18:00"
    bed: str = "21:30"


def _t(hhmm: str) -> datetime:
    return datetime.strptime(hhmm, "%H:%M")


def _fmt(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def dose_times(med: Med, r: Routine) -> list[str]:
    """Clock times for one scheduled med, anchored to the person's routine."""
    n, kind = med.times_per_day, med.kind
    if kind == "bedtime":
        return [r.bed]
    if kind == "evening":
        return [r.dinner]
    if kind == "interval" and n:
        step = timedelta(hours=24 / n)
        return [_fmt(_t(r.wake) + i * step) for i in range(n)]
    if n == 1:
        return [r.wake]
    if n == 2:
        return [r.wake, r.bed if med.with_food is not True else r.dinner]
    if n == 3:
        return [r.wake, r.lunch if med.with_food else _fmt(_t(r.wake) + (_t(r.bed) - _t(r.wake)) / 2), r.bed
                if med.with_food is not True else r.dinner]
    if n == 4:
        span = (_t(r.bed) - _t(r.wake)) / 3
        return [_fmt(_t(r.wake) + i * span) for i in range(4)]
    return []


def label(med: Med) -> str:
    name = med.drug or "medicine"
    strength = f" {med.strength}" if med.strength else ""
    qty = ""
    if med.dose is not None:
        q = "½" if med.dose == 0.5 else f"{med.dose:g}"
        form = med.dose_form or ""
        qty = f" · take {q} {form}{'s' if med.dose > 1 and form and not form.endswith('s') else ''}".rstrip()
    food = " · with food" if med.with_food else " · empty stomach" if med.with_food is False else ""
    return f"{name}{strength}{qty}{food}"


@dataclass
class Plan:
    by_time: dict[str, list[tuple[Med, str]]]
    as_needed: list[Med]
    ask: list[Med]
    warnings: list[str]


def build_plan(meds: list[Med], routine: Routine | None = None) -> Plan:
    r = routine or Routine()
    by_time: dict[str, list[tuple[Med, str]]] = defaultdict(list)
    warnings = []
    names = defaultdict(list)
    for m in meds:
        if m.drug:
            names[m.drug.lower()].append(m)
    for name, ms in names.items():
        if len(ms) > 1:
            warnings.append(f"{ms[0].drug} appears on {len(ms)} labels. Taking two at once could double the dose; "
                            "ask the pharmacist whether both are current.")
            for m in ms:
                m.ask("this medicine appears on more than one label")
    for m in meds:
        if m.decision != "schedule":
            continue
        note = {"alt": " (every other day)", "weekly": " (once a week)"}.get(m.kind or "", "")
        for t in dose_times(m, r):
            by_time[t].append((m, label(m) + note))
    return Plan(dict(sorted(by_time.items())), [m for m in meds if m.decision == "as-needed"],
                [m for m in meds if m.decision == "ask"], warnings)


def to_ics(plan: Plan, start: date | None = None) -> str:
    start = start or date.today()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//medsched//EN", "CALSCALE:GREGORIAN"]
    for t, items in plan.by_time.items():
        for m, text in items:
            hh, mm = t.split(":")
            rule = "FREQ=DAILY"
            if m.kind == "alt":
                rule = "FREQ=DAILY;INTERVAL=2"
            elif m.kind == "weekly":
                rule = "FREQ=WEEKLY"
            if m.duration_days:
                count = m.duration_days if m.kind not in ("alt", "weekly") else max(1, m.duration_days // (2 if m.kind == "alt" else 7))
                rule += f";COUNT={count}"
            summary = re.sub(r"[,;\\]", " ", text)
            lines += ["BEGIN:VEVENT", f"UID:{uuid.uuid4()}@medsched", f"DTSTAMP:{stamp}",
                      f"DTSTART:{start.strftime('%Y%m%d')}T{hh}{mm}00", "DURATION:PT10M", f"RRULE:{rule}",
                      f"SUMMARY:{summary}", "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{summary}",
                      "TRIGGER:PT0M", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def to_html(plan: Plan, title: str = "My medicine schedule") -> str:
    def clock(t):
        h, m = map(int, t.split(":"))
        return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"

    rows = "".join(
        f'<tr><td class="time">{clock(t)}</td><td>' + "".join(
            f'<div class="item"><span class="box"></span>{html.escape(text)}</div>' for _, text in items) + "</td></tr>"
        for t, items in plan.by_time.items())
    prn = "".join(f"<li><b>{html.escape(label(m))}</b> — only if needed"
                  f"{' for ' + html.escape(m.prn_reason) if m.prn_reason else ''}"
                  f"{f'. No more than {m.max_per_day:g} a day.' if m.max_per_day else ''}</li>" for m in plan.as_needed)
    ask = "".join(f"<li><b>{html.escape(((m.drug or m.raw[:40]) + (' ' + m.strength if m.strength else '')))}</b>: " + "; ".join(map(html.escape, m.reasons)) + "</li>"
                  for m in plan.ask)
    warn = "".join(f"<li>{html.escape(w)}</li>" for w in plan.warnings)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{font:20px/1.5 system-ui,sans-serif;color:#111;background:#fff;max-width:820px;margin:0 auto;padding:24px 16px}}
h1{{font-size:30px;margin:0 0 4px}} h2{{font-size:24px;margin:28px 0 8px}}
table{{width:100%;border-collapse:collapse}} td{{border-top:2px solid #222;padding:12px 8px;vertical-align:top}}
td.time{{width:130px;font-weight:700;font-size:24px}} .item{{margin:4px 0}}
.box{{display:inline-block;width:20px;height:20px;border:2px solid #222;border-radius:4px;margin-right:10px;vertical-align:-3px}}
.ask{{background:#fff4e5;border:2px solid #d48a00;border-radius:10px;padding:8px 18px 8px 40px}}
.warn{{background:#fdeaea;border:2px solid #b42318;border-radius:10px;padding:8px 18px 8px 40px}}
.foot{{font-size:15px;color:#444;margin-top:28px}}
@media print{{.foot{{font-size:12px}} body{{padding:0}}}}
</style></head><body>
<h1>{html.escape(title)}</h1><div>Tick each box when you take it.</div>
{f'<h2>⚠ Check first</h2><ul class="warn">{warn}</ul>' if warn else ''}
<table>{rows or '<tr><td>Nothing could be scheduled automatically.</td></tr>'}</table>
{f'<h2>Only when needed</h2><ul>{prn}</ul>' if prn else ''}
{f'<h2>Ask your pharmacist before using these</h2><ul class="ask">{ask}</ul>' if ask else ''}
<p class="foot">Made with medsched, an organizing aid. It is not medical advice and can make mistakes.
Always follow your prescription label and check this schedule with your pharmacist or doctor.</p>
</body></html>"""
