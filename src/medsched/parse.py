"""Parse pharmacy label directions ("sig") and decide whether they are safe to schedule.

The parser is built around one rule: when anything about the dose, timing or
drug is unclear, it does not guess. It returns `decision="ask"` with plain-language
reasons, and the item is routed to "ask your pharmacist" instead of the schedule.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "half": 0.5, "one-half": 0.5,
             "one half": 0.5, "a": 1, "an": 1}
_FORMS = r"(tablets?|tabs?|capsules?|caps?|pills?|puffs?|drops?|ml|mL|teaspoons?|tsp|patch(?:es)?|units?|sprays?)"

# ISMP list of error-prone abbreviations that should never be interpreted silently
ERROR_PRONE = {
    r"\bQ\.?D\.?\b": "'QD' (once daily) is easily misread as 'QID' (four times daily)",
    r"\bQ\.?O\.?D\.?\b": "'QOD' (every other day) is easily misread as 'QD' or 'QID'",
    r"\b\d+\s*U\b": "'U' for units can be misread as 0 or 4",
    r"\bI\.?U\.?\b": "'IU' can be misread as 'IV' or '10'",
    r"\b\d+\.0\s*(?:mg|mcg|g|ml)\b": "trailing zero (e.g. '1.0 mg') can be misread as ten times the dose",
    r"(?<![\d.])\.\d+\s*(?:mg|mcg|g|ml)\b": "missing leading zero (e.g. '.5 mg') can be misread as five",
    r"\bμg\b": "'μg' can be misread as 'mg'",
    r"\bHS\b": "'HS' can mean bedtime or half-strength",
}

FREQUENCIES = [
    # (pattern, label, times-per-day or None, kind)
    (r"\bevery other day\b", "every other day", 1, "alt"),
    (r"\b(?:once (?:a )?week(?:ly)?|every week|weekly)\b", "once a week", 1, "weekly"),
    (r"\b(?:four times (?:a |per |each )?day|four times daily|4 times (?:a |per )?day)\b", "four times a day", 4, "daily"),
    (r"\b(?:three times (?:a |per |each )?day|three times daily|3 times (?:a |per )?day)\b", "three times a day", 3, "daily"),
    (r"\b(?:twice (?:a |per |each )?day|twice daily|two times (?:a |per )?day|2 times (?:a |per )?day)\b", "twice a day", 2, "daily"),
    (r"\bevery 4 hours\b", "every 4 hours", 6, "interval"),
    (r"\bevery 6 hours\b", "every 6 hours", 4, "interval"),
    (r"\bevery 8 hours\b", "every 8 hours", 3, "interval"),
    (r"\bevery 12 hours\b", "every 12 hours", 2, "interval"),
    (r"\bevery 24 hours\b", "once a day", 1, "daily"),
    (r"\b(?:at bedtime|every night at bedtime|nightly|before bed)\b", "at bedtime", 1, "bedtime"),
    (r"\b(?:every morning|in the morning|each morning)\b", "every morning", 1, "morning"),
    (r"\b(?:every evening|in the evening|each evening)\b", "every evening", 1, "evening"),
    (r"\b(?:once (?:a |per |each )?day|once daily|daily|every day|each day)\b", "once a day", 1, "daily"),
]

ROUTES = {"by mouth": "by mouth", "orally": "by mouth", "po": "by mouth", "inhale": "inhaled",
          "inhalation": "inhaled", "in each eye": "eye", "in the left eye": "eye", "in the right eye": "eye",
          "in each ear": "ear", "topically": "on the skin", "apply": "on the skin", "under the tongue": "under the tongue",
          "subcutaneously": "injection", "inject": "injection"}


@dataclass
class Med:
    raw: str
    drug: str | None = None
    strength: str | None = None
    dose: float | None = None
    dose_form: str | None = None
    route: str | None = None
    frequency: str | None = None
    times_per_day: int | None = None
    kind: str | None = None
    prn: bool = False
    prn_reason: str | None = None
    with_food: bool | None = None
    duration_days: int | None = None
    max_per_day: float | None = None
    weekdays: list[int] | None = None    # 0 = Monday ... 6 = Sunday, for "every Monday and Thursday"
    time_hint: str | None = None
    decision: str = "schedule"           # schedule | as-needed | ask
    reasons: list[str] = field(default_factory=list)
    confidence: float = 1.0              # lowest field confidence (OCR confidence feeds in)

    def ask(self, why: str):
        self.decision = "ask"
        if why not in self.reasons:
            self.reasons.append(why)

    def as_dict(self):
        return asdict(self)


_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _weekdays(low: str) -> list[int]:
    """Weekdays named in the directions ('every Monday and Thursday', 'on Sundays')."""
    # full day names only, so words like "sat" or "wed" in other senses never count
    days = set()
    for m in re.finditer(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", low):
        days.add(_DAY_INDEX[m.group(1)[:3]])
    return sorted(days)


def _num(tok: str) -> float | None:
    tok = tok.strip().lower()
    if tok in _WORD_NUM:
        return float(_WORD_NUM[tok])
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", tok)
    if m:
        return int(m.group(1)) / int(m.group(2))
    try:
        return float(tok)
    except ValueError:
        return None


def parse_label(text: str, ocr_confidence: float | None = None) -> Med:
    """Parse one medication's label text (drug line + directions)."""
    raw = " ".join(text.split())
    med = Med(raw=raw)
    low = raw.lower()

    # drug name and strength: first "<Name> <number><unit>" on the label
    m = re.search(r"\b([A-Za-z][A-Za-z-]+(?:\s+[A-Z][A-Za-z0-9-]+)?)\s+(\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|%|units?))"
                  r"(?:\s*/\s*\d+(?:\.\d+)?\s*(?:mg|ml))?", raw)
    if m and m.group(1).lower() not in {"take", "use", "inhale", "apply", "give", "instill"}:
        med.drug, med.strength = m.group(1).strip(), m.group(2).replace(" ", "")
    else:
        m2 = re.match(r"\s*([A-Za-z][A-Za-z-]+)", raw)
        if m2 and m2.group(1).lower() not in {"take", "use", "inhale", "apply", "give"}:
            med.drug = m2.group(1)

    # error-prone abbreviations: never interpret silently
    for pat, why in ERROR_PRONE.items():
        if re.search(pat, raw, 0 if "μ" in pat else re.I):
            med.ask(f"label uses an error-prone abbreviation: {why}")

    if re.search(r"\b(?:as directed|use as directed|take as directed|as instructed)\b", low):
        med.ask("the label says 'as directed', so the actual schedule isn't written down")
    if re.search(r"\b(?:then|taper|decrease by|increase by|reduce to|for \d+ days,? then)\b", low):
        med.ask("the dose changes over time (taper or step schedule); confirm each step with the pharmacist")
    if re.search(r"\b(?:sliding scale|per scale|based on (?:blood )?sugar)\b", low):
        med.ask("the dose depends on a measurement (e.g. blood sugar); this tool won't calculate it")

    # dose quantity
    dm = re.search(r"\b(?:take|use|inhale|instill|apply|give|place|chew|dissolve)\s+"
                   r"(\d+(?:\.\d+)?(?:\s*(?:-|to|or)\s*\d+(?:\.\d+)?)?|\d+\s*/\s*\d+|one[- ]half|half|one|two|three|four|five|an?)"
                   r"\s*" + _FORMS + r"?", low)
    if dm:
        q = dm.group(1)
        if re.search(r"(?:-|\bto\b|\bor\b)", q) and not re.fullmatch(r"one[- ]half", q):
            med.ask(f"the dose is a range ('{q}'); ask which amount to take")
        else:
            med.dose = _num(q)
        med.dose_form = (dm.group(2) or "").rstrip("s") or None
    elif not med.reasons:
        med.ask("couldn't find how much to take")

    # frequency
    found = []
    for pat, label, tpd, kind in FREQUENCIES:
        if re.search(pat, low):
            found.append((label, tpd, kind))
    labels = {f[0] for f in found}
    # "every morning and at bedtime" means twice a day
    if {"every morning", "at bedtime"} <= labels or {"every morning", "every evening"} <= labels:
        found = [("twice a day (morning and night)", 2, "daily")]
    elif len(labels) > 1:
        specific = [f for f in found if f[0] != "once a day"]  # 'daily' inside 'twice daily' etc.
        found = specific if len({f[0] for f in specific}) == 1 else found
    days = _weekdays(low)
    if days:
        multi = [f for f in found if f[0] not in ("once a week", "once a day") and (f[1] or 1) > 1]
        if multi:
            med.ask("the label combines specific weekdays with more than one dose a day; confirm the schedule")
        else:
            names = ", ".join(_DAY_NAMES[d] for d in days)
            med.frequency, med.times_per_day, med.kind, med.weekdays = f"every {names}", 1, "weekdays", days
            if any(f[0] == "at bedtime" for f in found):
                med.time_hint = "bedtime"
    elif len({f[0] for f in found}) > 1:
        med.ask("the label gives more than one frequency: " + ", ".join(sorted({f[0] for f in found})))
    elif found:
        med.frequency, med.times_per_day, med.kind = found[0]
    if med.kind == "daily" and med.times_per_day == 1 and re.search(r"\b(?:at noon|midday|with lunch|at lunch)\b", low):
        med.kind, med.frequency = "noon", "once a day at midday"

    prn = re.search(r"\b(?:as needed|if needed|when needed|prn)\b(?:\s+for\s+([a-z ]+?))?(?:[.,;]|$| do not| not to)", low)
    if prn:
        med.prn = True
        med.prn_reason = prn.group(1).strip() if prn.group(1) else None
        if med.decision != "ask":
            med.decision = "as-needed"

    if not med.frequency and not med.prn and med.decision != "ask":
        med.ask("couldn't find how often to take it")

    mx = re.search(r"(?:do not|don't|never) (?:take |use )?(?:more than|exceed)\s+(\d+(?:\.\d+)?|one|two|three|four|five|six|eight)"
                   r"\s*" + _FORMS + r"?\s*(?:in|per|a|each)\s*(?:24 hours|day)", low)
    if mx:
        med.max_per_day = _num(mx.group(1))

    for phrase, route in ROUTES.items():
        if re.search(r"\b" + re.escape(phrase) + r"\b", low):
            med.route = route
            break
    if re.search(r"\b(?:with food|with a meal|with meals|with breakfast|with dinner)\b", low):
        med.with_food = True
    elif re.search(r"\b(?:on an empty stomach|before (?:breakfast|meals)|without food)\b", low):
        med.with_food = False
    dur = re.search(r"\bfor (\d+) days\b", low)
    if dur:
        med.duration_days = int(dur.group(1))

    # cross-checks
    if med.max_per_day and med.dose and med.times_per_day and med.decision == "schedule":
        if med.dose * med.times_per_day > med.max_per_day:
            med.ask(f"the schedule adds up to {med.dose * med.times_per_day:g} per day, more than the label's "
                    f"maximum of {med.max_per_day:g}")
    if med.dose is not None and med.dose > 4 and (med.dose_form or "") in {"tablet", "capsule", "pill", "tab", "cap"}:
        med.ask(f"{med.dose:g} {med.dose_form}s at once is unusual; double-check the label")

    if ocr_confidence is not None:
        med.confidence = min(med.confidence, ocr_confidence)
        if ocr_confidence < 0.80:
            med.ask(f"the photo was hard to read (OCR confidence {ocr_confidence:.0%}); type the label in or check it")
    return med
