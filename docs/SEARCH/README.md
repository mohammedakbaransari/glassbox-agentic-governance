# Documentation Search Index

This directory contains search index files for quick lookup across the GlassBox documentation.

## 📖 Search Strategy

The GlassBox documentation uses multiple search approaches:

### 1. **Built-in Search (Recommended)**
Use your browser's built-in search (Ctrl+F / Cmd+F) on:
- [docs/README.md](../README.md) - Main index with keyword listings
- [docs/VERSIONS/README.md](../VERSIONS/README.md) - Version-based search

### 2. **Topic-Based Navigation**
Navigate by topic through the search index sections in:
- [docs/README.md](../README.md#-search-index-by-topic)

### 3. **Role-Based Learning Paths**
Find your role in:
- [docs/README.md](../README.md#-learning-paths-by-role)

### 4. **Full-Text Search (CLI)**
From repo root:
```bash
# Search all docs for a term
grep -r "your_search_term" docs/

# Count occurrences
grep -r "your_search_term" docs/ | wc -l

# Show file + line
grep -rn "your_search_term" docs/

# Regex search
grep -rE "pattern|variation" docs/
```

### 5. **Category-Specific Search**
Each docs/ subdirectory has its own README with local index:
- [API/README.md](../API/README.md) - Search API docs
- [USER/README.md](../USER/README.md) - Search user guides
- [DEVELOPMENT/README.md](../DEVELOPMENT/README.md) - Search developer docs
- [FEATURES/README.md](../FEATURES/README.md) - Search features
- [DEPLOYMENT/README.md](../DEPLOYMENT/README.md) - Search deployment
- [COMPLIANCE/README.md](../COMPLIANCE/README.md) - Search compliance
- [SECURITY/README.md](../SECURITY/README.md) - Search security
- [PROCESSES/README.md](../PROCESSES/README.md) - Search processes

---

## 🔍 Keyword Index

### A
- **Adapters** → [DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md), [DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)
- **Anomaly Detection** → [DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md), [FEATURES/enterprise.md](../FEATURES/enterprise.md)
- **API Key** → [API/README.md](../API/README.md), [API/endpoint_reference.md](../API/endpoint_reference.md)
- **Audit Trail** → [COMPLIANCE/README.md](../COMPLIANCE/README.md), [FEATURES/enterprise.md](../FEATURES/enterprise.md)
- **Authentication** → [API/endpoint_reference.md](../API/endpoint_reference.md), [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Authorization** → [SECURITY/hardening.md](../SECURITY/hardening.md)

### B
- **Backup** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Benchmark** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Business Rules** → [USER/quick_start.md](../USER/quick_start.md), [FEATURES/README.md](../FEATURES/README.md)

### C
- **Circuit Breaker** → [FEATURES/velocity_breaker_readme.md](../FEATURES/velocity_breaker_readme.md)
- **CLI** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Compliance** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **Configuration** → [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)
- **Containerization** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Currency** → [FEATURES/velocity_breaker_readme.md](../FEATURES/velocity_breaker_readme.md)

### D
- **Database** → [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)
- **Decision** → [DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md), [USER/quick_start.md](../USER/quick_start.md)
- **Decision Replay** → [FEATURES/enterprise.md](../FEATURES/enterprise.md)
- **Deployment** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Disaster Recovery** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Distributed** → [FEATURES/velocity_breaker_details.md](../FEATURES/velocity_breaker_details.md)
- **Docker** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### E
- **Encryption** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Environment** → [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)
- **Error Handling** → [USER/troubleshooting.md](../USER/troubleshooting.md)
- **Example** → [USER/use_cases.md](../USER/use_cases.md)
- **Extension** → [DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)

### F
- **Failover** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Financial** → [USER/use_cases.md](../USER/use_cases.md)

### G
- **GDPR** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **Getting Started** → [USER/quick_start.md](../USER/quick_start.md)
- **GlassBox** → [USER/quick_start.md](../USER/quick_start.md)
- **Governance** → [DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md)
- **Guide** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### H
- **Health** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **HIPAA** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **Horizontal Scaling** → [DEPLOYMENT/README.md](../DEPLOYMENT/README.md)

### I
- **Installation** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Integration** → [API/endpoint_reference.md](../API/endpoint_reference.md)
- **Internal** → [DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md)
- **Interpreter** → [DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)
- **ISO 27001** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)

### K
- **Kubernetes** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Key Management** → [SECURITY/hardening.md](../SECURITY/hardening.md)

### L
- **Learning Path** → [docs/README.md](../README.md)
- **Load Balancer** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Lock** → [DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md)
- **Logging** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### M
- **Metrics** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Migration** → [VERSIONS/README.md](../VERSIONS/README.md)
- **Monitoring** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Multi-Tenant** → [FEATURES/enterprise.md](../FEATURES/enterprise.md)

### N
- **Network** → [SECURITY/hardening.md](../SECURITY/hardening.md)

### O
- **Operations** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### P
- **PCI DSS** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **Performance** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Pipeline** → [DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md)
- **Policy** → [USER/quick_start.md](../USER/quick_start.md), [DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)
- **PostgreSQL** → [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)
- **Profiling** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)

### Q
- **Quick Start** → [USER/quick_start.md](../USER/quick_start.md)

### R
- **RBAC** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Redis** → [FEATURES/velocity_breaker_readme.md](../FEATURES/velocity_breaker_readme.md), [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)
- **Regression** → [FEATURES/enterprise.md](../FEATURES/enterprise.md)
- **Release** → [PROCESSES/README.md](../PROCESSES/README.md)
- **Reliability** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
- **Response Time** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Risk** → [DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md)
- **Rollback** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### S
- **Scenario** → [USER/use_cases.md](../USER/use_cases.md)
- **Scaling** → [DEPLOYMENT/README.md](../DEPLOYMENT/README.md)
- **Schema** → [DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md)
- **Secrets** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Security** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **SOC 2** → [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
- **SSL/TLS** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **State** → [FEATURES/velocity_breaker_details.md](../FEATURES/velocity_breaker_details.md)
- **Status** → [DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)

### T
- **Telemetry** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Testing** → [DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)
- **Thread** → [DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md)
- **Throughput** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
- **Timeout** → [USER/troubleshooting.md](../USER/troubleshooting.md)
- **TLS** → [SECURITY/hardening.md](../SECURITY/hardening.md)
- **Troubleshoot** → [USER/troubleshooting.md](../USER/troubleshooting.md)
- **Tune** → [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)

### U
- **Upgrade** → [VERSIONS/COMPARISON.md](../VERSIONS/COMPARISON.md)
- **Use Case** → [USER/use_cases.md](../USER/use_cases.md)

### V
- **Velocity Breaker** → [FEATURES/velocity_breaker_readme.md](../FEATURES/velocity_breaker_readme.md)
- **Version** → [VERSIONS/README.md](../VERSIONS/README.md)
- **Vertical Scaling** → [DEPLOYMENT/README.md](../DEPLOYMENT/README.md)

### W
- **Workflow** → [FEATURES/enterprise.md](../FEATURES/enterprise.md)

### X-Z
- **YAML** → [DEPLOYMENT/deployment_reference.md](../DEPLOYMENT/deployment_reference.md)

---

## 📊 Search Statistics

| Category | Documents | Keywords | Topics |
|----------|-----------|----------|--------|
| API | 2 | 45+ | REST, endpoints, auth |
| USER | 4 | 60+ | Guides, examples, help |
| DEVELOPMENT | 3 | 55+ | Architecture, design, code |
| FEATURES | 4 | 50+ | Enterprise, VB, advanced |
| DEPLOYMENT | 4 | 60+ | Ops, config, monitoring |
| COMPLIANCE | 2 | 40+ | Regulations, audit, HIPAA |
| SECURITY | 2 | 50+ | Hardening, encryption, RBAC |
| PROCESSES | 2 | 35+ | Workflows, QA, teams |
| **Total** | **32** | **~395** | **All aspects** |

---

## 🔗 Cross-Document Links

Most documents link to related topics for easy navigation:

- See "Related Documentation" section in each document
- Breadcrumb navigation in all documents
- Table of Contents at top of longer docs
- Quick links in README sections

---

## 🎯 How to Search Effectively

### Local Browser Search (Ctrl+F)
Best for: Quick lookup in one doc
1. Open the relevant docs/*/README.md
2. Use Ctrl+F (Cmd+F on Mac)
3. Type search term
4. Click through matches

### Grep (Terminal)
Best for: Finding docs about a topic
```bash
# Find all docs mentioning "velocity"
grep -r "velocity" docs/

# Find docs with "policy" and show line
grep -rn "policy" docs/

# Case-insensitive search
grep -ri "encryption" docs/
```

### GitHub Search
Best for: Cross-repo searching
```
repo:mohammedakbaransari/glassbox-agentic-governance "search term"
```

### Documentation Index
Best for: Topic-based navigation
1. Check [Topic-Based Search Index](#-search-index-by-topic) above
2. Or check [docs/README.md](../README.md) search index

---

## 💡 Tips for Better Search

1. **Use specific keywords** - "velocity breaker" better than "breaker"
2. **Check related sections** - Each doc links to related topics
3. **Start with README files** - They contain overviews and links
4. **Use category READMEs** - Better targeted results
5. **Check version docs** - Your version might have different info
6. **Try multiple searches** - Different docs use different terms

---

*GlassBox Documentation Search*
