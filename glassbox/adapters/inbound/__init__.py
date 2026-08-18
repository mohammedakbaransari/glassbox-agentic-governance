"""Marker package: inbound adapters (transport-facing, calling into ``app``).

Mirrors the ``adapters/outbound`` split. Nothing under this package is imported
by :mod:`glassbox.domain` or :mod:`glassbox.ports`; it depends on ``app``, never
the reverse.
"""

from __future__ import annotations
