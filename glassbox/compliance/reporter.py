"""
GlassBox — Compliance Reporting API  (v1.0.0)
=============================================
Generates automated compliance reports from ComplianceCatalogue evidence.

What this provides:
  1. ComplianceReporter — builds structured compliance posture reports
     from the evidence collected by pipeline.process() calls
  2. ReportExporter — exports reports as structured JSON (default) or
     formatted text for human review
  3. Flask blueprint — adds /compliance/* REST endpoints to the existing
     Flask API app if Flask is available

Report types:
  Framework Coverage Report:
    For each framework, shows: total controls, implemented, partial, gap,
    coverage percentage, and recent evidence count.

  Gap Analysis Report:
    All controls with status 'gap' — what is not yet addressed.
    Grouped by framework, with GlassBox mapping column for remediation guidance.

  Evidence Audit Trail:
    For a specific control, all decisions that provide evidence, with
    timestamps, decision IDs, agent IDs, and outcomes.

  Executive Summary:
    Single-page posture view: overall compliance score, frameworks with
    full coverage, frameworks with critical gaps, evidence volume last 30 days.

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


class ComplianceReporter:
    """
    Generates compliance reports from ComplianceCatalogue evidence.

    Usage:
        from glassbox.compliance.reporter import ComplianceReporter

        cat      = ComplianceCatalogue()
        pipeline = GovernancePipeline(compliance_catalogue=cat)

        # ... govern some decisions ...

        reporter = ComplianceReporter(cat)
        report   = reporter.framework_coverage()
        print(json.dumps(report, indent=2))
    """

    def __init__(self, catalogue):
        """
        Args:
            catalogue: ComplianceCatalogue instance with evidence collected
        """
        self.cat = catalogue

    def framework_coverage(
        self,
        framework: Optional[str] = None,
        include_evidence_counts: bool = True,
    ) -> Dict[str, Any]:
        """
        Per-framework coverage report.

        Returns:
            {
              "generated_at": "...",
              "frameworks": {
                "NIST AI RMF": {
                  "total": 5, "implemented": 4, "partial": 1, "gap": 0,
                  "coverage_pct": 90.0, "evidence_count": 42
                }, ...
              },
              "overall_coverage_pct": 78.3
            }
        """
        posture = self.cat.posture_summary()
        if framework:
            posture = {k: v for k, v in posture.items() if k == framework}

        frameworks_out = {}
        total_controls = total_covered = 0

        for fw, data in posture.items():
            entry = dict(data)

            if include_evidence_counts:
                controls = self.cat.list_controls(framework=fw)
                evidence_count = 0
                for ctrl in controls:
                    ev = self.cat.get_evidence(ctrl["control_id"])
                    evidence_count += len(ev)
                entry["evidence_count"] = evidence_count

            frameworks_out[fw] = entry
            applicable = data["total"] - data.get("not_applicable", 0)
            total_controls += applicable
            total_covered  += data.get("implemented", 0) + data.get("partial", 0) * 0.5

        overall_pct = round(total_covered / max(total_controls, 1) * 100, 1)

        return {
            "generated_at":         datetime.now(timezone.utc).isoformat(),
            "report_type":          "framework_coverage",
            "frameworks":           frameworks_out,
            "overall_coverage_pct": overall_pct,
            "total_controls":       total_controls,
        }

    def gap_analysis(
        self,
        framework: Optional[str] = None,
        include_mapping: bool = True,
    ) -> Dict[str, Any]:
        """
        Returns all controls with status 'gap', grouped by framework.

        Returns:
            {
              "generated_at": "...",
              "total_gaps": 12,
              "gaps_by_framework": {
                "NERC CIP": [
                  {"control_id": "NERC.CIP007", "title": "...",
                   "glassbox_mapping": "...", "remediation_priority": "high"},
                  ...
                ]
              }
            }
        """
        gaps        = self.cat.gap_analysis(framework=framework)
        by_framework: Dict[str, List] = {}

        for ctrl in gaps:
            fw = ctrl["framework"]
            by_framework.setdefault(fw, [])
            entry = {
                "control_id": ctrl["control_id"],
                "category":   ctrl["category"],
                "title":      ctrl["title"],
                "description":ctrl["description"],
            }
            if include_mapping:
                entry["glassbox_mapping"] = ctrl.get("glassbox_mapping", "")
            # Assign remediation priority based on framework importance
            high_priority_frameworks = {
                "NIST AI RMF", "EU AI Act", "OWASP Agentic Top 10",
                "ASD Essential Eight"
            }
            entry["remediation_priority"] = (
                "high" if fw in high_priority_frameworks else "medium"
            )
            by_framework[fw].append(entry)

        return {
            "generated_at":       datetime.now(timezone.utc).isoformat(),
            "report_type":        "gap_analysis",
            "framework_filter":   framework or "all",
            "total_gaps":         len(gaps),
            "gaps_by_framework":  by_framework,
        }

    def evidence_audit_trail(
        self,
        control_id: str,
        limit:      int = 100,
    ) -> Dict[str, Any]:
        """
        Full evidence audit trail for a specific control.

        Shows every governed decision that provides evidence for the control,
        enabling auditors to trace compliance claims back to specific operations.

        Returns:
            {
              "control_id": "EUAI.A12",
              "control_title": "...",
              "evidence_count": 42,
              "evidence": [
                {"evidence_id": "...", "decision_id": "...",
                 "agent_id": "...", "collected_at": "...",
                 "evidence_type": "decision",
                 "summary": "procurement - executed - risk 8.5"},
                ...
              ]
            }
        """
        ctrl     = self.cat.get_control(control_id)
        evidence = self.cat.get_evidence(control_id)

        items = []
        for ev in evidence[:limit]:
            ev_data = {}
            if ev.get("evidence_data"):
                try:
                    ev_data = json.loads(ev["evidence_data"]) if isinstance(ev["evidence_data"], str) else ev["evidence_data"]
                except (json.JSONDecodeError, TypeError):
                    pass

            summary = " - ".join(filter(None, [
                ev_data.get("decision_type"),
                ev_data.get("final_status"),
                f"risk {ev_data['risk_score']:.1f}" if ev_data.get("risk_score") is not None else None,
            ]))

            items.append({
                "evidence_id":   ev.get("evidence_id"),
                "decision_id":   ev.get("decision_id"),
                "agent_id":      ev.get("agent_id"),
                "evidence_type": ev.get("evidence_type"),
                "collected_at":  ev.get("collected_at"),
                "summary":       summary or "governed decision",
            })

        return {
            "generated_at":    datetime.now(timezone.utc).isoformat(),
            "report_type":     "evidence_audit_trail",
            "control_id":      control_id,
            "control_title":   ctrl.get("title", "") if ctrl else "",
            "control_framework": ctrl.get("framework", "") if ctrl else "",
            "evidence_count":  len(evidence),
            "showing":         min(limit, len(evidence)),
            "evidence":        items,
        }

    def executive_summary(self, lookback_days: int = 30) -> Dict[str, Any]:
        """
        Single-page executive posture summary.

        Suitable for board-level reporting, regulatory submissions,
        or compliance dashboard widgets.

        Returns:
            {
              "overall_coverage_pct": 78.3,
              "total_frameworks":     11,
              "fully_covered":        ["NIST AI RMF", "OWASP Agentic Top 10"],
              "critical_gaps":        ["NERC CIP", "IEC 62443"],
              "evidence_volume_30d":  847,
              "decisions_governed_30d": 1203,
              ...
            }
        """
        coverage   = self.framework_coverage(include_evidence_counts=True)
        all_frameworks = coverage["frameworks"]

        fully_covered  = []
        critical_gaps  = []
        total_evidence = 0

        for fw, data in all_frameworks.items():
            total_evidence += data.get("evidence_count", 0)
            pct = data.get("coverage_pct", 0)
            if pct >= 80:
                fully_covered.append(fw)
            elif pct < 40:
                critical_gaps.append(fw)

        # Count gaps
        gaps   = self.gap_analysis()
        n_gaps = gaps["total_gaps"]

        return {
            "generated_at":           datetime.now(timezone.utc).isoformat(),
            "report_type":            "executive_summary",
            "lookback_days":          lookback_days,
            "overall_coverage_pct":   coverage["overall_coverage_pct"],
            "total_frameworks":       len(all_frameworks),
            "total_controls":         coverage["total_controls"],
            "controls_with_gaps":     n_gaps,
            "fully_covered_frameworks": fully_covered,
            "critical_gap_frameworks":  critical_gaps,
            "evidence_volume_total":  total_evidence,
            "frameworks_detail":      {
                fw: {"coverage_pct": d.get("coverage_pct", 0),
                     "evidence_count": d.get("evidence_count", 0),
                     "gap_count": d.get("gap", 0)}
                for fw, d in all_frameworks.items()
            }
        }

    def full_report(self) -> Dict[str, Any]:
        """Generate all four report types in a single call."""
        return {
            "executive_summary":   self.executive_summary(),
            "framework_coverage":  self.framework_coverage(),
            "gap_analysis":        self.gap_analysis(),
            "frameworks":          self.cat.frameworks_list(),
        }


class ReportExporter:
    """
    Exports ComplianceReporter output in various formats.

    Usage:
        reporter = ComplianceReporter(catalogue)
        exporter = ReportExporter(reporter)

        # JSON (default)
        json_str = exporter.to_json(exporter.reporter.executive_summary())

        # Formatted text for console or email
        text_str = exporter.executive_summary_text()

        # Save to file
        exporter.save_json("compliance_report.json", exporter.reporter.full_report())
    """

    def __init__(self, reporter: ComplianceReporter):
        self.reporter = reporter

    def to_json(self, report: Dict[str, Any], indent: int = 2) -> str:
        """Serialise a report dict to JSON string."""
        return json.dumps(report, indent=indent, default=str)

    def save_json(self, filepath: str, report: Optional[Dict] = None) -> str:
        """Save report to a JSON file. Returns the filepath."""
        if report is None:
            report = self.reporter.full_report()
        content = self.to_json(report)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    def executive_summary_text(self) -> str:
        """Format executive summary as readable plain text."""
        r = self.reporter.executive_summary()
        lines = [
            "=" * 60,
            "GlassBox Compliance Executive Summary",
            f"Generated: {r['generated_at'][:19].replace('T',' ')} UTC",
            "=" * 60,
            f"Overall Coverage:     {r['overall_coverage_pct']}%",
            f"Total Frameworks:     {r['total_frameworks']}",
            f"Total Controls:       {r['total_controls']}",
            f"Open Gaps:            {r['controls_with_gaps']}",
            f"Evidence Records:     {r['evidence_volume_total']}",
            "",
            "Frameworks with Strong Coverage (>80%):",
        ]
        for fw in r["fully_covered_frameworks"]:
            d = r["frameworks_detail"].get(fw, {})
            lines.append(f"  ✓  {fw}: {d.get('coverage_pct',0)}%")
        lines.append("")
        lines.append("Frameworks with Critical Gaps (<40%):")
        for fw in r["critical_gap_frameworks"]:
            d = r["frameworks_detail"].get(fw, {})
            lines.append(f"  !  {fw}: {d.get('coverage_pct',0)}% ({d.get('gap_count',0)} gaps)")
        lines += ["", "=" * 60]
        return "\n".join(lines)


def create_compliance_blueprint(catalogue, url_prefix: str = "/compliance"):
    """
    Create a Flask Blueprint that exposes compliance reporting endpoints.

    Endpoints added:
      GET /compliance/summary          — Executive summary
      GET /compliance/coverage         — Framework coverage report
      GET /compliance/gaps             — Gap analysis
      GET /compliance/evidence/<ctrl>  — Evidence audit trail for a control
      GET /compliance/report           — Full combined report

    Usage:
        from glassbox.compliance.reporter import create_compliance_blueprint
        blueprint = create_compliance_blueprint(catalogue)
        app.register_blueprint(blueprint)
    """
    try:
        from flask import Blueprint, jsonify, request
    except ImportError:
        raise ImportError("Flask is required for compliance blueprint: pip install flask")

    bp       = Blueprint("compliance", __name__, url_prefix=url_prefix)
    reporter = ComplianceReporter(catalogue)

    @bp.route("/summary")
    def summary():
        days = int(request.args.get("days", 30))
        return jsonify(reporter.executive_summary(lookback_days=days))

    @bp.route("/coverage")
    def coverage():
        fw = request.args.get("framework")
        return jsonify(reporter.framework_coverage(framework=fw))

    @bp.route("/gaps")
    def gaps():
        fw = request.args.get("framework")
        return jsonify(reporter.gap_analysis(framework=fw))

    @bp.route("/evidence/<control_id>")
    def evidence(control_id):
        limit = int(request.args.get("limit", 100))
        return jsonify(reporter.evidence_audit_trail(control_id, limit=limit))

    @bp.route("/report")
    def full_report():
        return jsonify(reporter.full_report())

    @bp.route("/frameworks")
    def frameworks():
        return jsonify({"frameworks": catalogue.frameworks_list()})

    return bp
