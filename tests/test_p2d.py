#!/usr/bin/env python3
"""Test P2-D: ecosystem_status method on VelocityBreaker"""

from glassbox.governance.models import EcosystemBreakerConfig
from glassbox.governance.velocity_breaker import VelocityBreaker


def main() -> None:
    breaker1 = VelocityBreaker(max_decisions=10, window_seconds=60)
    status1 = breaker1.ecosystem_status()
    print("Test 1 - Without ecosystem config:")
    print(f"Status: {status1}")
    assert status1["mode"] == "local"
    assert status1["agents_tracked"] == 0

    eco_config = EcosystemBreakerConfig(enabled=True, max_decisions=50)
    breaker2 = VelocityBreaker(
        max_decisions=10,
        window_seconds=60,
        ecosystem_max=eco_config.max_decisions,
        ecosystem_window_seconds=eco_config.window_seconds,
    )

    breaker2.check("agent1")
    breaker2.check("agent2")
    breaker2.check("agent1")

    status2 = breaker2.ecosystem_status()
    print("\nTest 2 - With ecosystem decisions:")
    print(f"Status: {status2}")
    assert status2["mode"] == "local"
    assert status2["agents_tracked"] == 2
    assert status2["agents_in_cooldown"] == 0
    assert status2["current_ecosystem_count"] == 3

    print("\nAll P2-D tests passed!")


if __name__ == "__main__":
    main()

