"""Forecast arithmetic — pure functions (no database), unit-tested on the host.

The line works `work_days` (ISO weekdays) between `work_start` and `work_end` (Settings). A working day's output is
spread evenly over that window, so "units per working day" turns into a finish time within the day.

day_fraction_left(now, ws, we, days)        share of today's working window still ahead (0 on a day off / after hours)
working_share(now, until, ws, we, days)     working time between two moments, in working days
working_days_between(now, last_day, …)      today's remaining share + the working days after today up to last_day
eta(now, remaining, per_day, ws, we, days)  when `remaining` units are done at `per_day` units per working day
recent_uph(hours, now, n)                   units per active hour over the last n hours that had tests (the hour still
                                            running is left out: it is not over yet)
last_working_days(today, n, days)           the n working days before today, newest first
"""
from datetime import date, datetime, time, timedelta
from typing import Optional

MAX_DAYS = 400          # an ETA further out than this is "not within a year"


def hm(text: str) -> time:
    h, m = str(text).split(":")
    return time(int(h), int(m))


def _window(day: date, ws: str, we: str) -> tuple[datetime, datetime]:
    return datetime.combine(day, hm(ws)), datetime.combine(day, hm(we))


def day_fraction_left(now: datetime, ws: str, we: str, days) -> float:
    if now.isoweekday() not in days:
        return 0.0
    a, b = _window(now.date(), ws, we)
    if now >= b:
        return 0.0
    return (b - max(now, a)).total_seconds() / (b - a).total_seconds()


def working_share(now: datetime, until: datetime, ws: str, we: str, days) -> float:
    """Working time between two moments, in working days (each working day's window counts as 1)."""
    if until <= now:
        return 0.0
    total, d = 0.0, now.date()
    while d <= until.date():
        if d.isoweekday() in days:
            a, b = _window(d, ws, we)
            lo, hi = max(a, now), min(b, until)
            if hi > lo:
                total += (hi - lo).total_seconds() / (b - a).total_seconds()
        d += timedelta(days=1)
    return round(total, 4)


def working_days_between(now: datetime, last_day: date, ws: str, we: str, days) -> float:
    """Working time from now to the end of last_day, in working days (today counts with its remaining share)."""
    if last_day < now.date():
        return 0.0
    total = day_fraction_left(now, ws, we, days)
    d = now.date() + timedelta(days=1)
    while d <= last_day:
        if d.isoweekday() in days:
            total += 1
        d += timedelta(days=1)
    return round(total, 3)


def eta(now: datetime, remaining: float, per_day: float, ws: str, we: str, days) -> Optional[dict]:
    """{"at": datetime, "working_days": float} for `remaining` units at `per_day` units per working day; None when it
    cannot finish (no rate, no working days, or beyond MAX_DAYS)."""
    if remaining <= 0:
        return {"at": now, "working_days": 0.0}
    if not per_day or per_day <= 0 or not days:
        return None
    need = remaining / per_day            # working days of output still needed
    spent = 0.0
    d = now.date()
    for i in range(MAX_DAYS):
        share = day_fraction_left(now, ws, we, days) if i == 0 else (1.0 if d.isoweekday() in days else 0.0)
        if share > 0:
            if spent + share >= need - 1e-9:
                a, b = _window(d, ws, we)
                start = max(now, a) if i == 0 else a
                at = start + (b - a) * (need - spent)
                return {"at": min(at, b), "working_days": round(need, 2)}
            spent += share
        d += timedelta(days=1)
    return None


def recent_uph(hours: list[tuple[datetime, int]], now: datetime, n: int = 6) -> tuple[Optional[float], int]:
    """(units per active hour, hours used) over the last n hours that had tests, the current hour left out."""
    current = now.replace(minute=0, second=0, microsecond=0)
    done = [(h, c) for h, c in hours if c and h < current]
    used = done[-n:]
    if not used:
        return None, 0
    return round(sum(c for _, c in used) / len(used), 1), len(used)


def last_working_days(today: date, n: int, days) -> list[date]:
    out, d = [], today - timedelta(days=1)
    while len(out) < n and days and (today - d).days < 400:
        if d.isoweekday() in days:
            out.append(d)
        d -= timedelta(days=1)
    return out
