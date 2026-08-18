"""Control-testing jobs over Delta Gold (GB-032).

Runs over Gold-shaped decision facts (GB-031), never over the live decision
path. Every function here is pure and takes its control definitions as a plain
argument rather than importing them: :mod:`glassbox.compliance` is a v1 package
that ``tests/test_layering.py`` forbids a rebuilt outbound adapter from
importing, and control-test *logic* has no reason to depend on where the
control catalogue's data happens to live. The caller -- a script or notebook
outside this package -- supplies the mapping.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

__all__ = ["evaluate_control", "denial_reason_distribution", "exposure_aggregate"]


def evaluate_control(row: Mapping[str, Any], control: Mapping[str, Any]) -> Dict[str, Any]:
    """Test one Gold decision-fact row against one control definition.

    Args:
        row: A Gold row -- at minimum ``decision_effect``, ``reasons`` (an
            iterable of denial-reason strings) and ``action``.
        control: ``{"control_id": ..., "applies_to_actions": (glob patterns,),
            "requires_effect": "deny"|"allow"|None}``. Pure data, no callables --
            unlike v1's 35 Python policy callables, a control definition here
            cannot be turned into a way to run arbitrary code on the cluster.

    Returns:
        ``{"control_id", "decision_id", "passed"}``. ``passed`` is ``True`` when
        the control does not apply to this row's action, so "not applicable" is
        never conflated with "failed".
    """
    import fnmatch

    action = row.get("action", "")
    patterns = control.get("applies_to_actions", ("*",))
    applies = any(fnmatch.fnmatchcase(action, pattern) for pattern in patterns)
    required_effect = control.get("requires_effect")
    passed = not applies or required_effect is None or row.get("decision_effect") == required_effect
    return {
        "control_id": control.get("control_id"),
        "decision_id": row.get("decision_id"),
        "applies": applies,
        "passed": bool(passed),
    }


def denial_reason_distribution(rows: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """Count denial reasons across a batch of Gold rows.

    Pure aggregation, no I/O -- suitable as the body of an RDD ``reduce`` or a
    Spark ``mapPartitions`` combined with a driver-side merge.
    """
    counts: Dict[str, int] = {}
    for row in rows:
        if row.get("decision_effect") != "deny":
            continue
        for reason in row.get("reasons", ()):
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def exposure_aggregate(rows: Iterable[Mapping[str, Any]]) -> Dict[str, float]:
    """Sum monetary exposure by consequence class across a batch of Gold rows."""
    totals: Dict[str, float] = {}
    for row in rows:
        consequence = row.get("consequence_class")
        monetary = row.get("exposure_monetary")
        if consequence is None or monetary is None:
            continue
        totals[consequence] = totals.get(consequence, 0.0) + float(monetary)
    return totals
