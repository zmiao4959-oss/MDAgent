"""Compatibility facade for :mod:`miniclaw.settings`.

Internal code uses ``miniclaw.settings``; this module keeps older integrations
working during the migration.
"""

from .settings import *  # noqa: F401,F403
