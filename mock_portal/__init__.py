"""Local mock portal used to exercise the rpa.formfill automation.

The package is self contained and only uses the Python standard library.
Configuration lives in portal.json next to this file (accounts, seed
records, field limits) so the portal can be regenerated with the same
rules but different concrete values.
"""

from .server import PortalServer

__all__ = ["PortalServer"]

