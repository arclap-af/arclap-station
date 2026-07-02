"""Gallery bulk delete + pagination support."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def test_delete_matching_removes_whole_set(fresh_db: Any, tmp_path: Path) -> None:
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    store = get_store()
    for i in range(5):
        store.register(tmp_path / f"p{i}.jpg", size_bytes=10)
    assert store.count() == 5
    # "Delete all" must hit the whole set, not just the first page.
    assert store.delete_matching() == 5
    assert store.count() == 0


def test_delete_matching_respects_filter(fresh_db: Any, tmp_path: Path) -> None:
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    store = get_store()
    a = store.register(tmp_path / "a.jpg", size_bytes=10)
    store.set_upload_state(a.id, "done")               # uploaded
    store.register(tmp_path / "b.jpg", size_bytes=10)  # pending
    deleted = store.delete_matching(upload_filter="uploaded")
    assert deleted == 1
    assert store.count() == 1  # the pending one survives


def test_list_pagination_offset(fresh_db: Any, tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from arclap_station.photos.store import get_store  # noqa: PLC0415

    store = get_store()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    for i in range(10):
        store.register(tmp_path / f"p{i}.jpg", size_bytes=10, captured_at=base + timedelta(minutes=i))
    page1 = store.list(limit=4, offset=0)
    page2 = store.list(limit=4, offset=4)
    assert len(page1) == 4 and len(page2) == 4
    assert {p.id for p in page1}.isdisjoint({p.id for p in page2})  # no overlap
    assert store.count() == 10
