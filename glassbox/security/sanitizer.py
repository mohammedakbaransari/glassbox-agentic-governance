"""
GlassBox Security Module  (v1.0.1)
Input sanitisation, injection detection, and payload security validation.

Protects against:
  - SQL injection in payload string values
  - Script/template injection (XSS, SSTI, command injection)
  - Path traversal attacks
  - Excessively large payloads (DoS via memory exhaustion)
  - Null byte injection
  - Unicode homoglyph attacks in identifiers
  - Deeply nested or excessively wide payloads

Fixes in v1.0.1:
  - Serialization errors now fail-closed (block) instead of silent pass
  - Added logging for security events

Usage:
    from glassbox.security.sanitizer import PayloadSanitizer, SecurityReport

    sanitizer = PayloadSanitizer()
    report = sanitizer.check(payload)
    if report.blocked:
        # reject the decision

Author: Mohammed Akbar Ansari
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from glassbox.governance.logging_manager import get_logger

log = get_logger("security")


# ── SQL injection patterns ────────────────────────────────────────────────────
# Matches common SQL injection vectors — not a full WAF, but catches the
# most common patterns in enterprise contexts.

_SQL_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?i)(\b(select|insert|update|delete|drop|truncate|alter|create"
               r"|exec|execute|xp_|sp_)\b)"),
    re.compile(r"(?i)(--\s|;.*(select|insert|update|delete|drop)|/\*.*\*/)"),
    re.compile(r"(?i)\b(or|and)\b\s+[\w'\"]+\s*=\s*[\w'\"]+"),   # OR 1=1
    re.compile(r"(?i)\bunion\b.*\bselect\b"),
    re.compile(r"(?i)\bsleep\s*\(|benchmark\s*\(|waitfor\b"),
    re.compile(r"(?i)\bchar\s*\(\s*\d+"),                          # CHAR(65)
    re.compile(r"(?i)\b(xp_cmdshell|openrowset|bulk\s+insert)\b"),
    # Extended patterns
    re.compile(r"(?i)\binto\s+outfile\b"),                         # MySQL file write
    re.compile(r"(?i)\bload_file\s*\("),                           # MySQL file read
    re.compile(r"0x[0-9a-fA-F]{4,}"),                             # Hex-encoded payloads
    re.compile(r"(?i)\bwaitfor\s+delay\b"),                        # MSSQL time-based blind
    re.compile(r"(?i)\bpg_sleep\s*\("),                            # PostgreSQL time-based
    re.compile(r"(?i)/\*[^*]*\*+(?:[^/*][^*]*\*+)*/"),           # Block comment bypass
    re.compile(r"(?i)\bcast\s*\(.*\bas\b"),                        # CAST type confusion
    re.compile(r"(?i)\bconvert\s*\(.*,\s*(int|varchar|char)\b"),   # CONVERT coercion
]

# ── Script / template injection patterns ────────────────────────────────────
_SCRIPT_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?i)<\s*script\b"),                               # XSS
    re.compile(r"(?i)javascript\s*:"),                             # JS URL
    re.compile(r"\{\{.*?\}\}"),                                    # Jinja/Twig SSTI
    re.compile(r"\$\{.*?\}"),                                      # EL injection
    re.compile(r"(?i)(eval|exec|compile|__import__)\s*\("),        # Python exec
    re.compile(r"(?i)(system|popen|subprocess|os\.)\s*\("),        # Shell
    re.compile(r"(?i)(\.\./|\.\.\\)"),                            # Path traversal
    re.compile(r"\x00"),                                           # Null byte
]

# ── Known malicious keywords ──────────────────────────────────────────────────
# All entries MUST be lowercase; case-insensitive matching is applied in
# _scan_string via s.lower(). Adding uppercase variants is NOT needed.
_BLOCKED_KEYWORDS: List[str] = [
    # File system & credential targets
    "passwd", "/etc/shadow", "/etc/hosts", "/proc/self",
    # Windows shell execution
    "cmd.exe", "powershell", "net user", "net localgroup",
    # Recon tools
    "whoami", "nmap", "netstat", "ifconfig", "ipconfig",
    # Encoding-based bypass helpers
    "base64_decode", "base64_encode", "fromcharcode",
    # Python dangerous builtins — not covered by regex (no parens required for presence check)
    "__import__", "__builtins__", "__class__", "__subclasses__",
    # Serialisation-based code execution
    "pickle.loads", "marshal.loads", "yaml.load", "jsonpickle",
    # Common reverse-shell tokens
    "/dev/tcp", "/dev/udp", "bash -i", "sh -i", "nc -e",
]

# ── Encoding bypass detection patterns ────────────────────────────────────────
# Detects payloads that encode dangerous content to slip past keyword filters.
_ENCODING_PATTERNS: List[re.Pattern] = [
    # Pure base64 blobs longer than 40 chars (likely encoded payload, not a GUID)
    re.compile(r"(?:[A-Za-z0-9+/]{4}){10,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"),
    # Percent-encoded hex sequences (URL encoding of non-printable / script chars)
    re.compile(r"(?:%[0-9a-fA-F]{2}){6,}"),
    # Unicode escape sequences: eval → "eval"
    re.compile(r"(?:\\u[0-9a-fA-F]{4}){4,}"),
    # HTML entity encoding of angle brackets (XSS bypass): &#60; &#62;
    re.compile(r"(?:&#x?[0-9a-fA-F]{2,4};){3,}"),
]


@dataclass
class SecurityFinding:
    severity:   str    # "critical", "high", "medium", "low"
    category:   str    # "sql_injection", "script_injection", "size", etc.
    field_path: str    # dot-notation path in payload
    detail:     str


@dataclass
class SecurityReport:
    """
    Result of a payload security scan.

    blocked: bool — True if the payload should be rejected.
    findings: list of SecurityFinding with details.
    clean_payload: the sanitised payload (strings truncated, HTML-escaped).
    """
    blocked:       bool
    findings:      List[SecurityFinding] = field(default_factory=list)
    clean_payload: Optional[Dict[str, Any]] = None

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "high")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blocked":      self.blocked,
            "findings":     [
                {"severity": f.severity, "category": f.category,
                 "field_path": f.field_path, "detail": f.detail}
                for f in self.findings
            ],
            "critical":     self.critical_count,
            "high":         self.high_count,
        }


class PayloadSanitizer:
    """
    Stateless, thread-safe payload security scanner and sanitiser.

    All methods are pure functions over their inputs — no shared state,
    no locks needed.

    Args:
        max_string_length:  Maximum characters per string value (default 4096).
        max_payload_depth:  Maximum nested dict/list depth (default 5).
        max_payload_keys:   Maximum keys per dict level (default 50).
        max_payload_size:   Maximum total JSON-serialised payload size in bytes.
        block_on_sql:       Block decision if SQL injection detected (default True).
        block_on_script:    Block decision if script injection detected (default True).
    """

    def __init__(
        self,
        max_string_length: int  = 4096,
        max_payload_depth: int  = 5,
        max_payload_keys:  int  = 50,
        max_payload_size:  int  = 8_192,    # 8 KB — aligned with API _MAX_BODY_BYTES
        block_on_sql:      bool = True,
        block_on_script:   bool = True,
    ):
        self.max_string_length = max_string_length
        self.max_payload_depth = max_payload_depth
        self.max_payload_keys  = max_payload_keys
        self.max_payload_size  = max_payload_size
        self.block_on_sql      = block_on_sql
        self.block_on_script   = block_on_script

    def check(self, payload: Any, agent_id: str = "") -> SecurityReport:
        """
        Scan payload for security issues and return a SecurityReport.
        Non-blocking — reports findings without raising exceptions.
        """
        findings: List[SecurityFinding] = []

        # Size check [v1.0.1 CRITICAL FIX] - Fail-closed on serialization error
        try:
            raw_size = len(json.dumps(payload, default=str).encode())
            if raw_size > self.max_payload_size:
                findings.append(SecurityFinding(
                    severity="high", category="size",
                    field_path="<root>",
                    detail=f"Payload size {raw_size} bytes exceeds limit {self.max_payload_size}"
                ))
        except Exception as exc:
            # [v1.0.1 CRITICAL] Fail-closed: payload cannot be serialized, treat as critical
            log.warning(
                f"Sanitizer: payload serialization error (fail-closed): {exc}",
                extra={"component": "security", "agent_id": agent_id}
            )
            findings.append(SecurityFinding(
                severity="critical", category="serialization",
                field_path="<root>",
                detail=f"Payload cannot be serialized: {exc}"
            ))

        # Structural checks + content scanning
        self._scan_value(payload, "<root>", 0, findings)

        # Determine if blocked
        block = False
        for f in findings:
            if f.severity == "critical":
                block = True
                break
            if f.severity == "high" and (
                (f.category == "sql_injection" and self.block_on_sql) or
                (f.category == "script_injection" and self.block_on_script)
            ):
                block = True
                break

        clean = self._sanitise(payload) if not block else None
        return SecurityReport(blocked=block, findings=findings, clean_payload=clean)

    def _scan_value(
        self,
        value:     Any,
        path:      str,
        depth:     int,
        findings:  List[SecurityFinding],
    ) -> None:
        if depth > self.max_payload_depth:
            findings.append(SecurityFinding(
                severity="high", category="depth",
                field_path=path,
                detail=f"Payload depth {depth} exceeds maximum {self.max_payload_depth}"
            ))
            return

        if isinstance(value, dict):
            if len(value) > self.max_payload_keys:
                findings.append(SecurityFinding(
                    severity="medium", category="width",
                    field_path=path,
                    detail=f"Dict has {len(value)} keys, maximum is {self.max_payload_keys}"
                ))
            for k, v in value.items():
                child_path = f"{path}.{k}"
                self._scan_string(str(k), f"{path}.<key>", findings)
                self._scan_value(v, child_path, depth + 1, findings)

        elif isinstance(value, list):
            for i, item in enumerate(value[:100]):   # limit scan to first 100 items
                self._scan_value(item, f"{path}[{i}]", depth + 1, findings)

        elif isinstance(value, str):
            self._scan_string(value, path, findings)

    def _scan_string(
        self,
        s:        str,
        path:     str,
        findings: List[SecurityFinding],
    ) -> None:
        if len(s) > self.max_string_length:
            findings.append(SecurityFinding(
                severity="medium", category="string_length",
                field_path=path,
                detail=f"String length {len(s)} exceeds max {self.max_string_length}"
            ))

        # Null byte
        if "\x00" in s:
            findings.append(SecurityFinding(
                severity="critical", category="null_byte",
                field_path=path,
                detail="Null byte detected in string value"
            ))

        # SQL injection
        for pattern in _SQL_PATTERNS:
            if pattern.search(s):
                findings.append(SecurityFinding(
                    severity="high", category="sql_injection",
                    field_path=path,
                    detail=f"SQL injection pattern detected: {pattern.pattern[:60]}"
                ))
                break  # one finding per field per category

        # Script/command injection
        for pattern in _SCRIPT_PATTERNS:
            if pattern.search(s):
                findings.append(SecurityFinding(
                    severity="high", category="script_injection",
                    field_path=path,
                    detail=f"Script injection pattern detected: {pattern.pattern[:60]}"
                ))
                break

        # Blocked keywords — case-insensitive match via s.lower().
        # Break after first to avoid finding spam.
        s_lower = s.lower()
        for kw in _BLOCKED_KEYWORDS:
            # kw is already lowercase; s_lower ensures case-insensitive comparison.
            if kw in s_lower:
                findings.append(SecurityFinding(
                    severity="critical", category="blocked_keyword",
                    field_path=path,
                    detail=f"Blocked keyword detected: '{kw}'"
                ))
                break  # one finding per string per category

        # Unicode normalisation check (homoglyph attack detection)
        # Apply NFKD (decomposed) normalization to catch look-alike confusables
        # such as Cyrillic 'с' (с) masquerading as Latin 'c'.
        nfkd = unicodedata.normalize("NFKD", s)
        nfkc = unicodedata.normalize("NFKC", s)
        if (nfkc != s or nfkd != s) and len(s) < 200:  # only check short strings (identifiers)
            findings.append(SecurityFinding(
                severity="medium", category="unicode_anomaly",
                field_path=path,
                detail="String contains Unicode characters that normalise differently (possible homoglyph)"
            ))
            # Re-check blocked keywords on the NFKC-normalised form to catch homoglyph bypasses.
            nfkc_lower = nfkc.lower()
            for kw in _BLOCKED_KEYWORDS:
                if kw in nfkc_lower:
                    findings.append(SecurityFinding(
                        severity="critical", category="blocked_keyword_homoglyph",
                        field_path=path,
                        detail=f"Blocked keyword '{kw}' detected after Unicode normalisation"
                    ))
                    break

        # Encoding bypass detection — flag suspicious encoded blobs
        # (long base64 strings, percent-encoded sequences, unicode escapes)
        for enc_pattern in _ENCODING_PATTERNS:
            if enc_pattern.search(s):
                findings.append(SecurityFinding(
                    severity="medium", category="encoding_bypass",
                    field_path=path,
                    detail=f"Possible encoding bypass detected: {enc_pattern.pattern[:60]}"
                ))
                break  # one finding per field per category

    def _sanitise(self, value: Any) -> Any:
        """Return a clean copy of the payload with strings truncated."""
        if isinstance(value, dict):
            return {k: self._sanitise(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._sanitise(i) for i in value]
        if isinstance(value, str):
            # Truncate and normalise
            s = unicodedata.normalize("NFKC", value)
            return s[:self.max_string_length]
        return value


# ── Identifier validator ──────────────────────────────────────────────────────

_SAFE_ID_RE = re.compile(r'^[a-zA-Z0-9_\-\.@:]+$')

def validate_agent_id(agent_id: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that an agent ID contains only safe characters.
    Rejects IDs that could be used for log injection or path traversal.
    """
    if not agent_id:
        return False, "agent_id must not be empty"
    if len(agent_id) > 128:
        return False, f"agent_id too long ({len(agent_id)} chars, max 128)"
    if not _SAFE_ID_RE.match(agent_id):
        return False, f"agent_id contains invalid characters: '{agent_id}'"
    return True, None


def sanitise_string(s: str, max_len: int = 512) -> str:
    """Normalise unicode and truncate a string to max_len."""
    return unicodedata.normalize("NFKC", str(s or ""))[:max_len]
