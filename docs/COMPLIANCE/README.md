# Compliance & Regulatory

This directory contains documentation for regulatory compliance, audit requirements, and compliance frameworks.

## 📖 Contents

### Compliance Requirements
- **[requirements.md](requirements.md)**
  - SOC 2 Type II requirements
  - ISO 27001 security standards
  - GDPR data privacy obligations
  - HIPAA healthcare regulations
  - PCI DSS financial standards
  - FCA regulatory capital rules

## 🏛️ Supported Frameworks

### SOC 2 Type II
**Audit Trail**: Full decision audit logging
- ✅ Availability - 99.9% uptime SLA
- ✅ Security - Encrypted storage
- ✅ Confidentiality - Access controls
- ✅ Integrity - Immutable audit logs
- ✅ Privacy - Data retention policies

**GlassBox Compliance**:
- Immutable decision audit trail
- Multi-tenant data isolation
- Role-based access control
- Encryption at rest & in transit
- Automated backup & recovery

### ISO 27001
**Information Security Management**

- ✅ Asset management - Comprehensive inventory
- ✅ Access control - RBAC policies
- ✅ Cryptography - Encryption support
- ✅ Physical security - Data center requirements
- ✅ Incident management - Logging & monitoring
- ✅ Business continuity - Failover support

**GlassBox Alignment**:
- Encrypted payload storage
- Multi-factor authentication support
- Comprehensive audit trails
- Disaster recovery infrastructure
- Regular security testing

### GDPR (EU Data Privacy)
**Right to**: Access, Erasure, Portability, Explanation

- ✅ Data Subject Access Requests (DSAR)
- ✅ Right to be forgotten
- ✅ Data portability export
- ✅ Decision explanation (Art. 13-15)
- ✅ Automated decision transparency

**GlassBox Features**:
- Decision explanation module
- Complete audit trail for DSAR
- Data retention policies (configurable)
- Export functionality (multiple formats)
- Consent tracking per decision

### HIPAA (US Healthcare)
**Protected Health Information (PHI) Protection**

- ✅ Authentication - MFA required
- ✅ Encryption - AES-256
- ✅ Access logs - Comprehensive tracking
- ✅ Breach notification - 60-day requirement
- ✅ Business associate agreements (BAA)

**GlassBox Compliance**:
- Encrypted decision payloads
- Detailed access audit logs
- Multi-tenant isolation
- Retention policies (HIPAA: 6 years minimum)
- Business Associate Agreement available

### PCI DSS (Payment Card Industry)
**Cardholder Data Protection**

- ✅ Encryption - Data at rest & transit
- ✅ Access control - Role-based
- ✅ Monitoring - Continuous logging
- ✅ Testing - Regular security assessments
- ✅ Documentation - Policies & procedures

**GlassBox Support**:
- Card data field encryption
- Payment decision audit trail
- PCI-level access controls
- Regular penetration testing integration
- Compliance certification support

## 📋 Compliance Configuration

### Enable Compliance Mode
```python
from glassbox.governance import GovernancePipeline

pipeline = GovernancePipeline(
    compliance_mode='HIPAA',  # or: SOC2, GDPR, PCI_DSS
    encryption_enabled=True,
    audit_retention_days=2555,  # 7 years
    encryption_key=os.getenv('GLASSBOX_ENCRYPTION_KEY')
)
```

### Compliance Audit Report
```python
from glassbox.compliance import ComplianceReporter

reporter = ComplianceReporter(pipeline)
report = reporter.generate_audit_report(
    start_date='2024-01-01',
    end_date='2024-03-31',
    framework='SOC2'
)
```

## 🔐 Security Controls

### Administrative Controls
- [ ] Written security policies
- [ ] Employee training program
- [ ] Incident response plan
- [ ] Access control procedures
- [ ] Regular risk assessments
- [ ] Security awareness training

### Technical Controls
- [ ] Encryption at rest (AES-256)
- [ ] Encryption in transit (TLS 1.2+)
- [ ] Multi-factor authentication
- [ ] Role-based access control (RBAC)
- [ ] Comprehensive audit logging
- [ ] Regular security patches

### Physical Controls
- [ ] Data center security
- [ ] Access badges & monitoring
- [ ] Surveillance cameras
- [ ] Secure disposal procedures
- [ ] Physical backup storage
- [ ] Environmental controls (fire, flood)

## 📊 Audit Requirements by Framework

| Framework | Audit Frequency | Log Retention | Encryption |
|-----------|-----------------|----------------|-----------|
| SOC 2 | Annual | 1 year | Recommended |
| ISO 27001 | 3 years | 1 year | Required |
| GDPR | Incident-driven | Legal hold | Recommended |
| HIPAA | Continuous | 6-10 years | Required |
| PCI DSS | Quarterly | 1 year | Required |

## 🔍 Audit Trail Features

### Decision Audit Log
Every decision includes:
- **Decision ID** - Unique identifier
- **Agent ID** - Who made the decision
- **Timestamp** - When it was evaluated
- **Decision Type** - What kind of decision
- **Payload** (encrypted if enabled)
- **Policies Evaluated** - Which rules applied
- **Risk Score** - Computed risk (0-100)
- **Disposition** - Final outcome
- **Explanation** - Why that disposition

### DSAR (Data Subject Access Request)
Request all decisions about a specific subject:
```python
from glassbox.compliance import DSARRequest

request = DSARRequest(
    subject_id='customer-12345',
    date_range=('2024-01-01', '2024-03-31')
)
records = request.execute()
# Returns: All decisions mentioning this subject
```

### Export Formats
Audit logs exportable in:
- **CSV** - Spreadsheet analysis
- **XML** - System integration
- **PDF** - Official documentation
- **JSON** - Programmatic access
- **JSONL** - Stream processing

## ⚖️ Regulatory Obligations

### Decision Explanation (GDPR Art. 13-15)
Every significant decision must be explainable:
```python
explanation = pipeline.explain_decision(decision_id)
# Returns:
# {
#   "decision": "BLOCK",
#   "reason": "Risk score 85 exceeds limit of 70",
#   "policies_applied": ["POL-005", "POL-012"],
#   "risk_factors": ["High amount", "New merchant"],
#   "appeal_process": "Customer service – 48h response"
# }
```

### Right to be Forgotten (GDPR)
Erase all data about a subject:
```python
from glassbox.compliance import RightToErasure

erase = RightToErasure(
    subject_id='customer-12345',
    reason='Customer requested erasure'
)
erase.execute()
# Deletes: All decisions, profiles, history about subject
```

### Data Portability (GDPR)
Export all personal data:
```python
from glassbox.compliance import DataPortability

export = DataPortability(
    subject_id='customer-12345',
    format='json'
)
data = export.execute()
# Returns: All data in machine-readable format
```

## 📈 Compliance Metrics

Monitor compliance KPIs:
- **Audit completeness** - % of decisions logged
- **Encryption coverage** - % of data encrypted
- **DSAR response time** - Days to respond
- **Incident response time** - Hours to detect
- **Patch currency** - % of systems current
- **Training completion** - % trained staff

## 📚 Related Documentation

- **Requirements detail**: [requirements.md](requirements.md)
- **Security hardening**: [../SECURITY/hardening.md](../SECURITY/hardening.md)
- **Deployment**: [../DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Audit features**: [../FEATURES/enterprise.md](../FEATURES/enterprise.md)

## 🤝 Compliance Support

For compliance questions:
1. Review [requirements.md](requirements.md)
2. Check relevant framework section above
3. See [../SECURITY/hardening.md](../SECURITY/hardening.md)
4. Contact compliance team


