"""Synthetic daily markup for legacy US execution-rule tests (not market evidence)."""
import datetime as dt


def markup_bars():
    rows = []
    for i in range(330):
        c = 10 * 1.004 ** i
        rows.append(dict(d=str(dt.date(2024, 1, 1) + dt.timedelta(days=i)),
                         o=c * .999, h=c * 1.02, l=c * .98, c=c, v=1000000))
    return rows


def with_verified_markup(decide):
    # Old tests isolate RR/chase/overnight behavior; explicitly provide the new
    # daily-data prerequisite. Gate truth-table tests call the raw engine.
    def run(*args, **kwargs):
        kwargs.setdefault("daily_bars", markup_bars())
        return decide(*args, **kwargs)
    return run
