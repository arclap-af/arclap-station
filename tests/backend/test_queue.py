"""Upload retry queue: enqueue → drain → ok / retry on failure."""

from __future__ import annotations

from pathlib import Path

from arclap_station.camera.adapter import get_adapter
from arclap_station.photos.store import get_store
from arclap_station.uploaders.manager import get_manager
from arclap_station.uploaders.queue import get_queue


def _photo(tmp_path: Path) -> int:
    p = get_adapter().capture(tmp_path)
    return get_store().register(p).id


def test_enqueue_and_drain_local(tmp_path: Path) -> None:
    dest = get_manager().create(
        "nas", "local", {"path": str(tmp_path / "out")}, enabled=True
    )
    photo_id = _photo(tmp_path)
    queue = get_queue()
    queue.enqueue(photo_id, [dest.id])
    processed = queue.drain_once()
    assert processed == 1
    items = queue.list()
    assert items[0].state == "ok"


def test_drain_marks_photo_done(tmp_path: Path) -> None:
    dest = get_manager().create(
        "nas", "local", {"path": str(tmp_path / "out")}, enabled=True
    )
    photo_id = _photo(tmp_path)
    queue = get_queue()
    queue.enqueue(photo_id, [dest.id])
    queue.drain_once()
    rec = get_store().get(photo_id)
    assert rec is not None
    assert rec.upload_state == "done"


def test_stats_reflect_state(tmp_path: Path) -> None:
    dest = get_manager().create(
        "nas", "local", {"path": str(tmp_path / "out")}, enabled=True
    )
    photo_id = _photo(tmp_path)
    queue = get_queue()
    queue.enqueue(photo_id, [dest.id])
    pre = queue.stats()
    assert pre.get("pending", 0) >= 1
    queue.drain_once()
    post = queue.stats()
    assert post.get("ok", 0) >= 1
