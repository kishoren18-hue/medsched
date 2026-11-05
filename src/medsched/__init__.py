"""medsched: turn pharmacy labels into a daily schedule, and refuse to guess when a label is unclear."""

from .parse import Med, parse_label
from .schedule import Plan, Routine, build_plan, takes_on, to_html, to_ics, to_weekly_html

__version__ = "0.2.0"
