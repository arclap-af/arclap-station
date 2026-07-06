"""Downloaded photos are named after their capture timestamp."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arclap_station.api.gallery import _timestamped_name


def test_prefixes_capture_timestamp() -> None:
    assert (
        _timestamped_name("2026-07-03T11:51:51.123+00:00", "capt0022.jpg")
        == "2026-07-03_11-51-51_capt0022.jpg"
    )


def test_handles_name_without_extension() -> None:
    assert (
        _timestamped_name("2026-07-03T09:41:00+00:00", "capt0000")
        == "2026-07-03_09-41-00_capt0000"
    )


def test_keeps_only_the_last_extension() -> None:
    assert (
        _timestamped_name("2026-07-03T07:00:00+00:00", "capt.raw.jpg")
        == "2026-07-03_07-00-00_capt.raw.jpg"
    )


def test_falls_back_to_original_when_timestamp_absent_or_partial() -> None:
    assert _timestamped_name("", "capt0022.jpg") == "capt0022.jpg"
    assert _timestamped_name("2026-07-03", "capt0022.jpg") == "capt0022.jpg"


def test_full_download_sets_timestamped_content_disposition(
    client: Any, app: Any, fresh_db: Any, tmp_path: Path
) -> None:
    from arclap_station.api.deps import require_session  # noqa: PLC0415
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    f = tmp_path / "capt0022.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0jpegish")  # real bytes so exists() passes
    rec = get_store().register(
        f, size_bytes=8, captured_at=datetime(2026, 7, 3, 11, 51, 51, tzinfo=UTC)
    )

    app.dependency_overrides[require_session] = lambda: {}
    try:
        r = client.get(f"/api/gallery/{rec.id}/full")
    finally:
        app.dependency_overrides.pop(require_session, None)

    assert r.status_code == 200
    assert "2026-07-03_11-51-51_capt0022.jpg" in r.headers.get("content-disposition", "")


def test_download_all_streams_zip_of_timestamped_photos(
    client: Any, app: Any, fresh_db: Any, tmp_path: Path
) -> None:
    import io
    import zipfile

    from arclap_station.api.deps import require_session  # noqa: PLC0415
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    store = get_store()
    for i, minute in enumerate((0, 10)):
        f = tmp_path / f"capt000{i}.jpg"
        f.write_bytes(b"\xff\xd8\xff" + bytes([i]) * 20)
        store.register(
            f, size_bytes=23, captured_at=datetime(2026, 7, 3, 11, minute, 0, tzinfo=UTC)
        )

    app.dependency_overrides[require_session] = lambda: {}
    try:
        r = client.get("/api/gallery/download-all")
    finally:
        app.dependency_overrides.pop(require_session, None)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == [
        "2026-07-03_11-00-00_capt0000.jpg",
        "2026-07-03_11-10-00_capt0001.jpg",
    ]
    # bytes survive the ZIP round-trip (STORED, no compression).
    assert zf.read("2026-07-03_11-00-00_capt0000.jpg") == b"\xff\xd8\xff" + bytes([0]) * 20
