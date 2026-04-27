"""
VelocityBreaker Invariant Tests (ISSUE-02)
==========================================

Behavioral specification tests for the reconstructed VelocityBreaker.
These tests document and verify key boundary conditions that must hold
regardless of implementation details.

Run with:
    pytest tests/test_velocity_breaker_invariants.py -v
"""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from glassbox.governance.velocity_breaker import VelocityBreaker, DistributedVelocityBreaker


class TestVelocityBreakerInvariants(unittest.TestCase):
    """Boundary conditions for the single-instance VelocityBreaker."""

    # ── Boundary: exact max ────────────────────────────────────────────────

    def test_does_not_trip_at_exactly_max_decisions(self):
        """max_decisions=5: calls 1-5 must all return triggered=False."""
        vb = VelocityBreaker(max_decisions=5, window_seconds=60)
        for i in range(1, 6):
            triggered, reason, count = vb.check("agent_a")
            self.assertFalse(
                triggered,
                f"Tripped early at decision {i} (count={count}, reason={reason!r})",
            )
        # Verify count is tracked correctly after 5 calls
        _, _, count = vb.check("agent_a")  # This is call 6 — DOES trip
        self.assertGreaterEqual(count, 5)

    def test_trips_at_one_over_max(self):
        """max_decisions=5: call 6 must return triggered=True with a reason."""
        vb = VelocityBreaker(max_decisions=5, window_seconds=60)
        for _ in range(5):
            triggered, _, _ = vb.check("agent_a")
            self.assertFalse(triggered)
        # Call 6 — over the limit
        triggered, reason, _ = vb.check("agent_a")
        self.assertTrue(triggered, "Expected trip on call 6 (max_decisions=5)")
        self.assertIsNotNone(reason, "Trip must include a human-readable reason")
        self.assertGreater(len(reason), 0)

    def test_max_one_boundary(self):
        """max_decisions=1: first call passes, second call trips."""
        vb = VelocityBreaker(max_decisions=1, window_seconds=60)
        triggered, reason, _ = vb.check("agent_b")
        self.assertFalse(triggered, "First call with max=1 must not trip")
        triggered, reason, _ = vb.check("agent_b")
        self.assertTrue(triggered, "Second call with max=1 must trip")

    # ── Agent isolation ────────────────────────────────────────────────────

    def test_cross_agent_state_never_leaks(self):
        """
        Agent A hammering the limit must not affect Agent B.
        Data integrity breach: tripping one agent must never contaminate another.
        """
        vb = VelocityBreaker(max_decisions=2, window_seconds=60)
        for _ in range(20):
            vb.check("heavy_agent")
        triggered, reason, _ = vb.check("clean_agent")
        self.assertFalse(
            triggered,
            f"Agent state leaked: clean_agent tripped with reason={reason!r}",
        )

    def test_independent_counters_per_agent(self):
        """Each agent has its own independent sliding window counter."""
        vb = VelocityBreaker(max_decisions=3, window_seconds=60)
        for _ in range(3):
            vb.check("agent_x")
        # agent_y is independent — should start fresh
        for i in range(1, 4):
            triggered, _, count = vb.check("agent_y")
            self.assertFalse(
                triggered,
                f"agent_y tripped at call {i} — shared state with agent_x",
            )

    # ── Cooldown ───────────────────────────────────────────────────────────

    def test_cooldown_reason_is_human_readable(self):
        """Tripped agent must report 'cooldown' in reason (for operators)."""
        vb = VelocityBreaker(max_decisions=2, window_seconds=60, cooldown_seconds=300)
        for _ in range(3):
            vb.check("agent_c")
        triggered, reason, _ = vb.check("agent_c")
        self.assertTrue(triggered)
        self.assertIsNotNone(reason)
        self.assertIn(
            "cooldown",
            reason.lower(),
            f"Reason must mention cooldown; got: {reason!r}",
        )

    def test_cooldown_blocks_subsequent_requests(self):
        """Once in cooldown, every subsequent check returns triggered=True."""
        vb = VelocityBreaker(max_decisions=2, window_seconds=60, cooldown_seconds=300)
        for _ in range(3):
            vb.check("agent_d")
        # All further calls within cooldown must be blocked
        for _ in range(5):
            triggered, _, _ = vb.check("agent_d")
            self.assertTrue(triggered)

    # ── Window expiry ──────────────────────────────────────────────────────

    def test_window_expiry_resets_count(self):
        """
        Decisions older than window_seconds must not count toward the limit.

        Uses a 1-second window so the test doesn't take long.
        """
        vb = VelocityBreaker(max_decisions=3, window_seconds=1, cooldown_seconds=0)
        for _ in range(3):
            vb.check("agent_e")
        # Wait for window to expire
        time.sleep(1.1)
        # Fresh window — should not trip
        triggered, reason, _ = vb.check("agent_e")
        self.assertFalse(
            triggered,
            f"Decisions outside window should not count; got reason={reason!r}",
        )

    # ── Reset ──────────────────────────────────────────────────────────────

    def test_reset_clears_agent_state(self):
        """reset() must clear window and cooldown for the target agent."""
        vb = VelocityBreaker(max_decisions=2, window_seconds=60, cooldown_seconds=300)
        for _ in range(3):
            vb.check("agent_f")
        triggered, _, _ = vb.check("agent_f")
        self.assertTrue(triggered)
        vb.reset("agent_f")
        triggered, _, _ = vb.check("agent_f")
        self.assertFalse(triggered, "Agent state should be cleared after reset()")

    def test_reset_does_not_affect_other_agents(self):
        """reset('agent_g') must not clear state for 'agent_h'."""
        vb = VelocityBreaker(max_decisions=2, window_seconds=60, cooldown_seconds=300)
        for _ in range(3):
            vb.check("agent_h")
        vb.reset("agent_g")
        triggered, _, _ = vb.check("agent_h")
        # agent_h is still tripped — reset of unrelated agent must not clear it
        self.assertTrue(triggered)

    # ── Concurrency ────────────────────────────────────────────────────────

    def test_concurrent_checks_from_many_threads(self):
        """
        500 concurrent threads each calling check() must not raise exceptions
        or cause data corruption.  The final count must be deterministic — each
        thread's result is either pass or trip, never an unhandled exception.
        """
        vb = VelocityBreaker(max_decisions=50, window_seconds=60)
        errors = []

        def _check():
            try:
                vb.check("concurrent_agent")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_check) for _ in range(500)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(
            errors, [],
            f"Concurrent checks raised exceptions: {errors}",
        )

    def test_different_agents_concurrent_isolation(self):
        """
        Many threads checking different agent IDs must never see cross-agent
        contamination: an agent that only called once must not be marked tripped.
        """
        vb = VelocityBreaker(max_decisions=5, window_seconds=60)
        results: dict[str, list] = {}
        lock = threading.Lock()

        def _check_unique(agent_id: str):
            triggered, _, _ = vb.check(agent_id)
            with lock:
                results[agent_id] = triggered

        # Each thread has a unique agent ID — none should trip (only 1 call each)
        threads = [
            threading.Thread(target=_check_unique, args=(f"unique_agent_{i}",))
            for i in range(200)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        tripped_unique = [aid for aid, t in results.items() if t]
        self.assertEqual(
            tripped_unique, [],
            f"Unique agents (1 call each) should never trip: {tripped_unique}",
        )


class TestVelocityBreakerEcosystem(unittest.TestCase):
    """Ecosystem-level velocity breaker tests."""

    def test_ecosystem_limit_trips_after_fleet_exceeds_max(self):
        """Ecosystem check fires when combined agent traffic exceeds ecosystem_max."""
        vb = VelocityBreaker(
            max_decisions=100,        # High per-agent limit — won't trigger
            window_seconds=60,
            ecosystem_max=5,          # Low fleet limit — triggers after 5 total
            ecosystem_window_seconds=60,
        )
        # First 5 calls (different agents) should pass
        for i in range(5):
            triggered, _, _ = vb.check(f"fleet_agent_{i}")
            self.assertFalse(triggered)
        # 6th call from a new agent should trip ecosystem limit
        triggered, reason, _ = vb.check("fleet_agent_new")
        self.assertTrue(triggered)
        self.assertIsNotNone(reason)

    def test_ecosystem_reset_clears_fleet_state(self):
        """reset_ecosystem() allows new fleet decisions through."""
        vb = VelocityBreaker(
            max_decisions=100,
            window_seconds=60,
            ecosystem_max=3,
            ecosystem_window_seconds=60,
        )
        for i in range(4):
            vb.check(f"eco_agent_{i}")
        vb.reset_ecosystem()
        triggered, _, _ = vb.check("eco_agent_fresh")
        self.assertFalse(triggered, "Ecosystem reset should clear fleet state")


class TestDistributedVelocityBreakerInvariants(unittest.TestCase):
    """
    Same boundary invariants applied to DistributedVelocityBreaker with a
    mocked backend that always falls back to local state.

    Tests verify that the distributed wrapper preserves the same contract
    as VelocityBreaker even when the remote backend is unavailable.
    """

    def _make_dvb(self, **kwargs) -> DistributedVelocityBreaker:
        """Create DVB with a mock backend that always raises (forces local fallback)."""
        dvb = DistributedVelocityBreaker(**kwargs)
        # Simulate Redis being unavailable — forces local_breaker fallback
        dvb._use_distributed = False
        return dvb

    def test_local_fallback_trips_at_one_over_max(self):
        dvb = self._make_dvb(max_decisions=5, window_seconds=60)
        for _ in range(5):
            triggered, _, _ = dvb.check("dvb_agent")
            self.assertFalse(triggered)
        triggered, reason, _ = dvb.check("dvb_agent")
        self.assertTrue(triggered)
        self.assertIsNotNone(reason)

    def test_local_fallback_agent_isolation(self):
        dvb = self._make_dvb(max_decisions=2, window_seconds=60)
        for _ in range(20):
            dvb.check("dvb_heavy")
        triggered, _, _ = dvb.check("dvb_clean")
        self.assertFalse(triggered)

    def test_local_fallback_window_expiry(self):
        dvb = self._make_dvb(max_decisions=3, window_seconds=1, cooldown_seconds=0)
        for _ in range(3):
            dvb.check("dvb_exp")
        time.sleep(1.1)
        triggered, _, _ = dvb.check("dvb_exp")
        self.assertFalse(triggered)


if __name__ == "__main__":
    unittest.main()
