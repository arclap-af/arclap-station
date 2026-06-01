"""Station health: self-test, alerting, heartbeat.

This package is the station's "is everything OK, and if not, why" layer.
It is deliberately self-contained and fail-soft: every check is wrapped
so a probe that itself errors degrades to an `unknown` result rather
than taking the whole self-test (or the API request that called it)
down.
"""
