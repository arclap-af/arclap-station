"""Audit log: hash-chain integrity + chain-preserving prune (bounded growth)."""

from __future__ import annotations

from typing import Any

from arclap_station import audit
from arclap_station.db import get_db


def test_chain_verifies_when_intact(fresh_db: Any) -> None:
    for i in range(10):
        audit.emit("system", f"e{i}", {"i": i})
    assert audit.verify_chain()["ok"]


def test_prune_bounds_and_preserves_chain(fresh_db: Any) -> None:
    for i in range(20):
        audit.emit("system", f"e{i}", {"i": i})
    assert audit.verify_chain()["ok"]

    assert audit.prune(keep=5) == 15
    with get_db().connect() as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]) == 5

    # The retained window must still verify as a continuous chain via the
    # saved anchor — not fail at its first row (whose prev points at a
    # pruned row).
    res = audit.verify_chain()
    assert res["ok"], res["breaks"]
    assert res["checked"] == 5

    # New events after a prune keep chaining cleanly.
    audit.emit("system", "after-prune", {})
    assert audit.verify_chain()["ok"]


def test_prune_noop_under_limit(fresh_db: Any) -> None:
    for i in range(3):
        audit.emit("system", f"e{i}")
    assert audit.prune(keep=100) == 0
    assert audit.verify_chain()["ok"]
