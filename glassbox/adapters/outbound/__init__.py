"""Outbound adapters (GB-003).

Concrete implementations of the ports in :mod:`glassbox.ports`. Adapters may
import :mod:`glassbox.domain`, :mod:`glassbox.ports` and third-party libraries.
Nothing in :mod:`glassbox.app` may import this package -- the composition root
receives adapter sets from the process entry point instead.
"""

from __future__ import annotations

__all__: list = []
