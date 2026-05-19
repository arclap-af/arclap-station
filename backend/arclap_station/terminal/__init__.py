"""Restricted PTY shell exposed over WebSocket."""

from arclap_station.terminal.pty import PTYNotSupported, RestrictedPTY

__all__ = ["RestrictedPTY", "PTYNotSupported"]
