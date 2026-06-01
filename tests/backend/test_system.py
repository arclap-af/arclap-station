"""System router — semver parsing for the update-check."""

from __future__ import annotations

from arclap_station.api.system import _parse_semver


def test_parse_semver_variants() -> None:
    assert _parse_semver("v0.9.0") == (0, 9, 0)
    assert _parse_semver("0.9.0") == (0, 9, 0)
    assert _parse_semver("v1.2.3") == (1, 2, 3)
    assert _parse_semver("v10.20.30") == (10, 20, 30)


def test_parse_semver_rejects_nonsemver() -> None:
    assert _parse_semver("latest") is None
    assert _parse_semver("v0.9") is None
    assert _parse_semver("0.9.0-rc1") is None
    assert _parse_semver("") is None


def test_semver_ordering_drives_update_available() -> None:
    """Tuple comparison is what update_check uses to decide if a newer
    tag exists. A running version AHEAD of the latest tag is 'current'."""
    running = _parse_semver("0.9.0")
    older_tag = _parse_semver("0.8.2")
    newer_tag = _parse_semver("0.10.0")
    assert running is not None and older_tag is not None and newer_tag is not None
    assert not (older_tag > running)   # running ahead of tag → no update
    assert newer_tag > running          # newer tag → update available
