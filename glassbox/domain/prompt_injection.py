"""Prompt-injection / indirect-injection heuristic control (GB-029).

Replaces one clause of the regex WAF this card retires: ``security/sanitizer.py``
(v1) ran the same SQL- and script-injection patterns over *every* payload
string, which is why a legitimate purchase order ("Create purchase order for Q3
and update the supplier record") and a hex account number
(``0xA1B2C3D4E5F6``) were both blocked, and why "Grupo \u00c1gua & Caf\u00e9
Ltda" registered as a unicode anomaly.

This module is never applied to a business field. It is invoked by
:class:`~glassbox.app.decision_service.DecisionService` only for parameters an
action's :class:`~glassbox.domain.catalogue.ActionDefinition` names in
``untrusted_text_fields`` -- content that originated from a model or an agent,
not a caller-supplied transactional fact. A business field is never scanned,
which is what keeps the false-positive rate at zero on a legitimate corpus
while still detecting instruction-override and role-delimiter smuggling on the
one class of content where it is a real risk.

Pure and deterministic, like the rest of :mod:`glassbox.domain`: no I/O, no
clock, no third-party imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern, Tuple

__all__ = ["PromptInjectionReport", "scan"]

#: Instruction-override, role-confusion and system-prompt-exfiltration patterns.
#: Deliberately narrow and scoped to this one purpose, unlike the SQL/script
#: pattern lists in v1's ``security/sanitizer.py``, which were never scoped to a
#: specific content class.
_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(ignore|disregard)\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+instructions\b"
    ),
    re.compile(r"(?i)\bnew\s+instructions\s*:"),
    re.compile(r"(?i)\byou\s+are\s+now\b.{0,40}\b(dan|jailbreak(?:en)?|unrestricted|unfiltered)\b"),
    re.compile(
        r"(?i)\bact\s+as\s+(if\s+you\s+are\s+)?(an?\s+)?(unfiltered|unrestricted|jailbroken)\b"
    ),
    re.compile(r"(?i)\b(reveal|print|repeat)\s+(the\s+)?(system\s+prompt|your\s+instructions)\b"),
    re.compile(r"(?i)</?\s*(system|assistant|user)\s*>"),
    re.compile(r"(?i)\bthis\s+is\s+a\s+(test|simulation)\s*[:,-].{0,40}\bignore\b"),
)


@dataclass(frozen=True, slots=True)
class PromptInjectionReport:
    """Result of scanning one untrusted-text field.

    Attributes:
        field: Name of the parameter that was scanned.
        flagged: Whether any pattern matched.
        matched_patterns: The pattern source strings that matched, for evidence
            and triage -- never the surrounding text, which may be sensitive.
    """

    field: str
    flagged: bool
    matched_patterns: Tuple[str, ...] = ()


def scan(field: str, text: str) -> PromptInjectionReport:
    """Scan one untrusted-text field's content for injection patterns.

    Args:
        field: Name of the parameter being scanned, carried through to the
            report so a caller scanning several fields can attribute a match.
        text: The field's content. Only ever a field an action's catalogue
            entry names as ``untrusted_text_fields`` -- never a business field.
    """
    matched = tuple(pattern.pattern for pattern in _PATTERNS if pattern.search(text))
    return PromptInjectionReport(field=field, flagged=bool(matched), matched_patterns=matched)
