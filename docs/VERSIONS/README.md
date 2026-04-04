# Documentation Versions

GlassBox documentation is maintained across versions to help you find information for your specific version.

## 📌 Current Versions

### ✅ [Latest (v1.1.0)](latest/)
**Current Stable Release** - Recommended for all new deployments

Latest documentation with all current features including:
- Enterprise features (multi-tenancy, workflow orchestration)
- Decision replay & regression testing
- Custom risk models
- Distributed velocity breaker (v1.0.1+)
- Performance optimizations

**Docs Format:** Full directory structure with all 8 categories  
**Use This Version If:** You're on v1.1.0 or deploying new

---

### 🔄 [v1.1.0](v1.1.0/)
**Enterprise Release** - Current production version

Features introduced:
- Multi-tenant isolation (TenantRegistry)
- Decision replay for regression testing
- Workflow orchestration
- Custom risk evaluation models
- Advanced telemetry
- Performance optimizations (95% anomaly detection speedup)

**Docs Format:** Full documentation set  
**Release Date:** April 2026  
**Use This Version If:** You're deploying or upgrading to v1.1

---

### 🔧 [v1.0.1](v1.0.1/)
**Pre-LTS Release** - Feature-complete with optimizations

Features introduced:
- Distributed velocity breaker
- Redis-backed rate limiting
- Circuit breaker pattern
- Performance optimizations (lock pooling, Welford's algorithm)

**Docs Format:** Documentation for core features + VB  
**Release Date:** January 2026  
**Use This Version If:** You're still on v1.0.1 (EOL: July 2026)

---

### 👴 [v1.0.0](v1.0.0/)
**Initial Release** - Core governance engine

Features:
- 9-stage decision pipeline
- Policy engine (24 built-in policies)
- Multi-tenancy support
- REST API
- Audit logging
- Risk scoring

**Docs Format:** Core documentation  
**Release Date:** October 2025  
**End of Life:** July 2025  
**Use This Version If:** Legacy deployment (requires upgrade)

---

## 🗂️ Version Structure

Each version directory contains:
```
docs/VERSIONS/v1.X.Y/
├── API/                       # API docs for this version
├── USER/                      # User guides
├── DEVELOPMENT/               # Technical documentation
├── FEATURES/                  # Version-specific features
├── DEPLOYMENT/                # Deployment procedures
├── COMPLIANCE/                # Compliance info
├── SECURITY/                  # Security hardening
├── PROCESSES/                 # Processes & workflows
├── MIGRATION.md               # Upgrade from previous version
├── CHANGELOG.md               # What changed in this version
└── README.md                  # Version-specific overview
```

---

## 🎯 Choose Your Version

| You Need | Go To |
|----------|-------|
| Latest features | [latest/](latest/) |
| v1.1.0 docs | [v1.1.0/](v1.1.0/) |
| v1.0.1 docs | [v1.0.1/](v1.0.1/) |
| v1.0.0 docs | [v1.0.0/](v1.0.0/) |
| Version comparison | [COMPARISON.md](COMPARISON.md) |
| Migration guide | See MIGRATION.md in your target version |

---

## 📈 Version Lifecycle

### Active Support
- **v1.1.0+** - Full support, latest features
- **v1.0.1** - Extended support until July 2026
- **v1.0.0** - End of life, no support

### Support Timeline
```
v1.0.0  |════════════════════════════════════| EOL: July 2025
v1.0.1  |════════════════════════════════════| EOL: July 2026
v1.1.0  |═══════════════════════════════════► Active support
```

---

## 🔄 Migration Guides

**Upgrading to v1.1.0 from v1.0.1?**  
See [v1.1.0/MIGRATION.md](v1.1.0/MIGRATION.md) for step-by-step guide

**Upgrading to v1.0.1 from v1.0.0?**  
See [v1.0.1/MIGRATION.md](v1.0.1/MIGRATION.md) for step-by-step guide

---

## 📋 Feature Comparison

| Feature | v1.0.0 | v1.0.1 | v1.1.0 |
|---------|--------|--------|--------|
| Core Pipeline | ✅ | ✅ | ✅ |
| Policy Engine | ✅ | ✅ | ✅ |
| REST API | ✅ | ✅ | ✅ |
| Multi-tenancy | ✅ | ✅ | ✅ |
| Distributed VB | — | ✅ | ✅ |
| Decision Replay | — | — | ✅ |
| Custom Risk Models | — | — | ✅ |
| Workflow Chains | — | — | ✅ |
| Performance Opts | — | ✅ | ✅ |

---

## 🚀 What's New in Each Version

### v1.1.0 (Latest)
```
✨ NEW FEATURES
├─ Multi-tenant registry with isolation
├─ Decision replay for regression testing
├─ Workflow orchestration (chains)
├─ Custom risk evaluation models
├─ Advanced telemetry per stage
└─ Enterprise adapters (Databricks, Kubernetes, Fabric)

⚡ OPTIMIZATIONS
├─ 95% faster anomaly detection
├─ Lock pooling for audit logging
├─ Snapshot pattern for policies
└─ Distributed state coordination

🔧 IMPROVEMENTS
├─ Type hints on all APIs
├─ Enhanced error messages
├─ Better performance monitoring
└─ Comprehensive audit trail
```

### v1.0.1 (Feature-complete)
```
✨ NEW FEATURES
├─ Distributed velocity breaker
├─ Redis-backed rate limiting
├─ Circuit breaker pattern
└─ Multi-instance coordination

⚡ OPTIMIZATIONS
├─ Lock pooling implementation
├─ Welford's algorithm (O(1) stats)
├─ Snapshot pattern
└─ 10-30% latency reduction

🔧 IMPROVEMENTS
├─ Better error handling
├─ Improved logging
└─ Performance benchmarks
```

### v1.0.0 (Foundation)
```
✨ INITIAL FEATURES
├─ 9-stage decision pipeline
├─ 24 built-in policies
├─ REST API
├─ Audit logging
├─ Risk scoring
└─ Multi-tenancy support
```

---

## 🔗 Documentation Links

### For Latest Version (v1.1.0)
- [Quick Start](latest/USER/quick_start.md)
- [Architecture](latest/DEVELOPMENT/architecture.md)
- [Deployment](latest/DEPLOYMENT/guide.md)
- [Security](latest/SECURITY/hardening.md)

### For Previous Versions
- [v1.0.1 Quick Start](v1.0.1/USER/quick_start.md)
- [v1.0.0 Quick Start](v1.0.0/USER/quick_start.md)

---

## ❓ FAQ

**Q: Which version should I use?**  
A: Use v1.1.0 (latest) for new deployments. Upgrade from v1.0.1 at your convenience (support until July 2026).

**Q: What if I'm on v1.0.0?**  
A: Please upgrade to v1.1.0. v1.0.0 is no longer supported.

**Q: Where are the docs for my version?**  
A: See the "Choose Your Version" section above.

**Q: How do I migrate between versions?**  
A: See MIGRATION.md in your target version directory.

**Q: Can I use docs from a different version?**  
A: Most docs apply across versions, but always check your version's CHANGELOG for breaking changes.

---

*GlassBox Documentation Versions*
