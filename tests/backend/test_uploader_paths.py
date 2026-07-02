"""Uploader Phase-1 fixes: path-template expansion + modern SFTP keys."""

from __future__ import annotations

from datetime import datetime

from arclap_station.uploaders import expand_placeholders


def test_expand_placeholders_expands_date_tokens() -> None:
    when = datetime(2026, 5, 9, 14, 30)
    assert expand_placeholders("photos/{yyyy}/{mm}/{dd}/", when) == "photos/2026/05/09/"
    assert expand_placeholders("{yyyy}-{mm}-{dd}_{HH}{MM}", when) == "2026-05-09_1430"


def test_expand_placeholders_noop_without_braces() -> None:
    # The default templates no longer carry placeholders — must pass through.
    assert expand_placeholders("photos/", None) == "photos/"
    assert expand_placeholders("", None) == ""
    assert expand_placeholders("/var/lib/arclap/local-photos", None) == "/var/lib/arclap/local-photos"


def test_no_literal_braces_survive() -> None:
    out = expand_placeholders("x/{yyyy}/{mm}/{dd}/{HH}/{station}", datetime(2026, 1, 2, 3, 4), "st1")
    assert "{" not in out and "}" not in out


def test_ftp_ftps_selection_actually_encrypts() -> None:
    """Regression: the form's default 'ftps_explicit' silently produced
    PLAINTEXT FTP because the backend only matched literal 'ftps'."""
    from arclap_station.uploaders.ftp import FTPUploader  # noqa: PLC0415

    assert FTPUploader("i", "n", {"host": "h", "security": "ftps_explicit"}).use_tls is True
    assert FTPUploader("i", "n", {"host": "h", "security": "ftps_implicit"}).use_tls is True
    assert FTPUploader("i", "n", {"host": "h", "security": "plain"}).use_tls is False
    # No Security field → plaintext unless the encrypt toggle is on.
    assert FTPUploader("i", "n", {"host": "h"}).use_tls is False
    assert FTPUploader("i", "n", {"host": "h", "encrypt_in_transit": True}).use_tls is True
    assert FTPUploader("i", "n", {"host": "h", "security": "ftps_implicit"}).implicit_tls is True


def test_sftp_loads_ed25519_key() -> None:
    """ssh-keygen's default key type (Ed25519) must load — the old
    RSAKey-only path failed on every modern key."""
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: PLC0415

    from arclap_station.uploaders.sftp import SFTPUploader  # noqa: PLC0415

    pem = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    up = SFTPUploader("i", "n", {"host": "h", "username": "u", "private_key": pem})
    loaded = up._load_private_key()  # noqa: SLF001
    assert loaded is not None


def test_sftp_bad_key_raises_uploaderror() -> None:
    from arclap_station.uploaders import UploadError  # noqa: PLC0415
    from arclap_station.uploaders.sftp import SFTPUploader  # noqa: PLC0415

    up = SFTPUploader("i", "n", {"host": "h", "username": "u", "private_key": "not a key"})
    try:
        up._load_private_key()  # noqa: SLF001
    except UploadError:
        return
    raise AssertionError("expected UploadError on garbage key")
