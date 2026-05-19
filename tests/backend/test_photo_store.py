"""PhotoStore basic CRUD + thumbnail generation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arclap_station.photos.store import get_store
from arclap_station.photos.thumbnails import generate_thumbnail


def _seed_photo(tmp_path: Path) -> Path:
    from arclap_station.camera.adapter import get_adapter  # noqa: PLC0415

    return get_adapter().capture(tmp_path)


def test_register_and_get(tmp_path: Path) -> None:
    path = _seed_photo(tmp_path)
    store = get_store()
    rec = store.register(path)
    assert rec.id > 0
    assert store.get(rec.id) is not None


def test_list_pagination(tmp_path: Path) -> None:
    store = get_store()
    for _ in range(3):
        path = _seed_photo(tmp_path)
        store.register(path)
    page = store.list(limit=2)
    assert len(page) == 2
    assert store.count() >= 3


def test_count_since(tmp_path: Path) -> None:
    store = get_store()
    path = _seed_photo(tmp_path)
    store.register(path)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    assert store.count_since(yesterday) >= 1


def test_thumbnail_generation(tmp_path: Path) -> None:
    path = _seed_photo(tmp_path)
    thumb = generate_thumbnail(path)
    assert thumb.exists()
    assert thumb.stat().st_size > 0


def test_delete_removes_record_and_file(tmp_path: Path) -> None:
    store = get_store()
    path = _seed_photo(tmp_path)
    rec = store.register(path)
    assert store.delete(rec.id)
    assert store.get(rec.id) is None
    assert not path.exists()
