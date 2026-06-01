"""Optional hardware integrations (UPS HAT, controllable USB hub, RTC).

Everything here is graceful-degrade: the station runs identically with
none of this hardware fitted. A reader returns "not present" rather than
raising, so the rest of the system never has to special-case a bare Pi.
"""
