# GlassBox Documentation Index

Welcome to the GlassBox Framework documentation. This folder is organized into logical sections for easy navigation.

**📊 Quick Stats:** 32 documentation files | 8 categories | 4 root files | 1.1.0

---

## 📚 Documentation Structure

### [API/](API/) - API Reference
**API Reference & Endpoint Documentation** (1 file)
- [API/README.md](API/README.md) - Navigation hub for API docs
- `endpoint_reference.md` - REST API endpoints, auth, rate limiting

### [USER/](USER/) - User Guides
**User Guides & Tutorials** (4 files)
- [USER/README.md](USER/README.md) - Navigation hub for user guides
- `quick_start.md` - Get started in 5 minutes
- `use_cases.md` - Real-world usage scenarios
- `troubleshooting.md` - Common issues and solutions

### [DEVELOPMENT/](DEVELOPMENT/) - Developers
**Developer Guides & Architecture** (3 files)
- [DEVELOPMENT/README.md](DEVELOPMENT/README.md) - Navigation hub for developers
- `architecture.md` - System design, 9-stage pipeline, components
- `implementation_guide.md` - Custom policies, adapters, extensions

### [FEATURES/](FEATURES/) - Features
**Feature Documentation** (4 files)
- [FEATURES/README.md](FEATURES/README.md) - Navigation hub for features
- `enterprise.md` - Enterprise features, workflow orchestration
- `velocity_breaker_readme.md` - Distributed velocity breaker overview
- `velocity_breaker_details.md` - Technical implementation details

### [DEPLOYMENT/](DEPLOYMENT/) - Operations
**Deployment & Operations** (4 files)
- [DEPLOYMENT/README.md](DEPLOYMENT/README.md) - Navigation hub for operations
- `guide.md` - Step-by-step deployment guide
- `deployment_reference.md` - Configurations and scaling
- `performance_tuning.md` - Optimization and benchmarking

### [COMPLIANCE/](COMPLIANCE/) - Compliance
**Regulatory & Compliance** (2 files)
- [COMPLIANCE/README.md](COMPLIANCE/README.md) - Navigation hub for compliance
- `requirements.md` - SOC 2, ISO 27001, GDPR, HIPAA, PCI

### [SECURITY/](SECURITY/) - Security
**Security Documentation** (2 files)
- [SECURITY/README.md](SECURITY/README.md) - Navigation hub for security
- `hardening.md` - Security best practices, encryption, RBAC

### [PROCESSES/](PROCESSES/) - Processes
**Workflows & Procedures** (2 files)
- [PROCESSES/README.md](PROCESSES/README.md) - Navigation hub for processes
- `review_and_improvements.md` - Code review, QA, release management

### Root Documentation (2 files)
- [CONTRIBUTING.md](CONTRIBUTING.md) - Contribution guidelines
- [glossary.md](glossary.md) - Key terms and definitions

---

## 🗂️ Complete File Tree

```
docs/
├── README.md                              ← You are here
├── CONTRIBUTING.md                        # How to contribute
├── glossary.md                            # Terminology reference
│
├── API/                                   # API Documentation
│   ├── README.md                          # API navigation hub
│   └── endpoint_reference.md              # REST API reference
│
├── USER/                                  # User Guides & Tutorials
│   ├── README.md                          # User section hub
│   ├── quick_start.md                     # 5-minute startup
│   ├── use_cases.md                       # Real-world examples
│   └── troubleshooting.md                 # Problem solving
│
├── DEVELOPMENT/                           # Developer Guides
│   ├── README.md                          # Development hub
│   ├── architecture.md                    # System design
│   └── implementation_guide.md            # Extension guide
│
├── FEATURES/                              # Feature Docs
│   ├── README.md                          # Features hub
│   ├── enterprise.md                      # Enterprise (v1.1+)
│   ├── velocity_breaker_readme.md         # VB overview (v1.0.1+)
│   └── velocity_breaker_details.md        # VB technical details
│
├── DEPLOYMENT/                            # Operations
│   ├── README.md                          # Deployment hub
│   ├── guide.md                           # Deploy step-by-step
│   ├── deployment_reference.md            # Config reference
│   └── performance_tuning.md              # Performance guide
│
├── COMPLIANCE/                            # Compliance
│   ├── README.md                          # Compliance hub
│   └── requirements.md                    # Regulatory requirements
│
├── SECURITY/                              # Security
│   ├── README.md                          # Security hub
│   └── hardening.md                       # Security hardening
│
└── PROCESSES/                             # Processes
    ├── README.md                          # Processes hub
    └── review_and_improvements.md         # Workflows & review

```

---

## 🎯 Learning Paths by Role

### 👨‍💼 Business User (30 minutes)
1. [USER/quick_start.md](USER/quick_start.md) - Understand what GlassBox does
2. [USER/use_cases.md](USER/use_cases.md) - See real examples
3. [FEATURES/README.md](FEATURES/README.md) - Learn capabilities

### 👨‍💻 Software Developer (2 hours)
1. [USER/quick_start.md](USER/quick_start.md) - Get running
2. [DEVELOPMENT/architecture.md](DEVELOPMENT/architecture.md) - Understand design
3. [DEVELOPMENT/implementation_guide.md](DEVELOPMENT/implementation_guide.md) - Build extensions
4. [API/endpoint_reference.md](API/endpoint_reference.md) - Integrate API

### 🔧 DevOps/Operations (1.5 hours)
1. [DEPLOYMENT/guide.md](DEPLOYMENT/guide.md) - Deploy to production
2. [DEPLOYMENT/performance_tuning.md](DEPLOYMENT/performance_tuning.md) - Optimize
3. [SECURITY/hardening.md](SECURITY/hardening.md) - Secure infrastructure
4. [DEPLOYMENT/README.md](DEPLOYMENT/README.md) - Operations procedures

### 🛡️ Security Team (1 hour)
1. [SECURITY/hardening.md](SECURITY/hardening.md) - Security practices
2. [COMPLIANCE/requirements.md](COMPLIANCE/requirements.md) - Regulatory alignment
3. [DEPLOYMENT/guide.md](DEPLOYMENT/guide.md) - Deployment security

### ⚖️ Compliance/Legal (45 minutes)
1. [COMPLIANCE/requirements.md](COMPLIANCE/requirements.md) - Full compliance view
2. [FEATURES/enterprise.md](FEATURES/enterprise.md) - Audit capabilities
3. [SECURITY/hardening.md](SECURITY/hardening.md) - Data protection

### 💡 Enterprise Architect (1.5 hours)
1. [DEVELOPMENT/architecture.md](DEVELOPMENT/architecture.md) - System design
2. [FEATURES/enterprise.md](FEATURES/enterprise.md) - Enterprise features
3. [DEPLOYMENT/README.md](DEPLOYMENT/README.md) - Deployment patterns
4. [COMPLIANCE/requirements.md](COMPLIANCE/requirements.md) - Compliance

---

## 🔍 Search Index (by Topic)

### Concepts & Architecture
- **Decision Pipeline** → [DEVELOPMENT/architecture.md](DEVELOPMENT/architecture.md)
- **Policies & Rules** → [USER/quick_start.md](USER/quick_start.md), [API/endpoint_reference.md](API/endpoint_reference.md)
- **Risk Scoring** → [DEVELOPMENT/architecture.md](DEVELOPMENT/architecture.md)
- **Multi-tenancy** → [FEATURES/enterprise.md](FEATURES/enterprise.md)

### Getting Started
- **First 5 minutes** → [USER/quick_start.md](USER/quick_start.md)
- **Installation** → [DEPLOYMENT/guide.md](DEPLOYMENT/guide.md)
- **First Decision** → [USER/quick_start.md](USER/quick_start.md)

### API Integration
- **API Overview** → [API/README.md](API/README.md)
- **Endpoints** → [API/endpoint_reference.md](API/endpoint_reference.md)
- **Authentication** → [API/endpoint_reference.md](API/endpoint_reference.md)
- **Rate Limiting** → [API/endpoint_reference.md](API/endpoint_reference.md)

### Extending GlassBox
- **Custom Policies** → [DEVELOPMENT/implementation_guide.md](DEVELOPMENT/implementation_guide.md)
- **Custom Adapters** → [DEVELOPMENT/implementation_guide.md](DEVELOPMENT/implementation_guide.md)
- **Pipeline Extensions** → [DEVELOPMENT/implementation_guide.md](DEVELOPMENT/implementation_guide.md)

### Performance & Operations
- **Configuration** → [DEPLOYMENT/deployment_reference.md](DEPLOYMENT/deployment_reference.md)
- **Performance Tuning** → [DEPLOYMENT/performance_tuning.md](DEPLOYMENT/performance_tuning.md)
- **Scaling** → [DEPLOYMENT/README.md](DEPLOYMENT/README.md)
- **Monitoring** → [DEPLOYMENT/guide.md](DEPLOYMENT/guide.md)

### Security & Compliance
- **Security Hardening** → [SECURITY/hardening.md](SECURITY/hardening.md)
- **Encryption** → [SECURITY/hardening.md](SECURITY/hardening.md)
- **Audit Trail** → [COMPLIANCE/requirements.md](COMPLIANCE/requirements.md)
- **Regulatory** → [COMPLIANCE/requirements.md](COMPLIANCE/requirements.md)
  - SOC 2, ISO 27001, GDPR, HIPAA, PCI DSS

### Enterprise Features
- **Overview** → [FEATURES/enterprise.md](FEATURES/enterprise.md)
- **Workflow Orchestration** → [FEATURES/enterprise.md](FEATURES/enterprise.md)
- **Decision Replay** → [FEATURES/enterprise.md](FEATURES/enterprise.md)
- **Custom Risk Models** → [FEATURES/enterprise.md](FEATURES/enterprise.md)

### Troubleshooting & Support
- **Common Issues** → [USER/troubleshooting.md](USER/troubleshooting.md)
- **Error Codes** → [USER/troubleshooting.md](USER/troubleshooting.md)
- **Performance** → [DEPLOYMENT/performance_tuning.md](DEPLOYMENT/performance_tuning.md)
- **Logging** → [DEPLOYMENT/guide.md](DEPLOYMENT/guide.md)

### Velocity Breaker (v1.0.1+)
- **Overview** → [FEATURES/velocity_breaker_readme.md](FEATURES/velocity_breaker_readme.md)
- **Technical Details** → [FEATURES/velocity_breaker_details.md](FEATURES/velocity_breaker_details.md)
- **Configuration** → [DEPLOYMENT/deployment_reference.md](DEPLOYMENT/deployment_reference.md)

### Versioning
- **Version History** → See CHANGELOG.md in project root
- **What's New in v1.0.1** → Velocity Breaker features + optimizations
- **What's New in v1.1** → Enterprise features, multi-tenancy, decision replay

---

## 🚀 Quick Links

| I Want To... | Start Here |
|--------------|-----------|
| Get started in 5 minutes | [USER/quick_start.md](USER/quick_start.md) |
| See real examples | [USER/use_cases.md](USER/use_cases.md) |
| Understand how it works | [DEVELOPMENT/architecture.md](DEVELOPMENT/architecture.md) |
| Deploy to production | [DEPLOYMENT/guide.md](DEPLOYMENT/guide.md) |
| Integrate with API | [API/endpoint_reference.md](API/endpoint_reference.md) |
| Build custom policies | [DEVELOPMENT/implementation_guide.md](DEVELOPMENT/implementation_guide.md) |
| Optimize performance | [DEPLOYMENT/performance_tuning.md](DEPLOYMENT/performance_tuning.md) |
| Secure my deployment | [SECURITY/hardening.md](SECURITY/hardening.md) |
| Meet compliance | [COMPLIANCE/requirements.md](COMPLIANCE/requirements.md) |
| Troubleshoot problems | [USER/troubleshooting.md](USER/troubleshooting.md) |
| Understand enterprise features | [FEATURES/enterprise.md](FEATURES/enterprise.md) |
| Contribute to GlassBox | [CONTRIBUTING.md](CONTRIBUTING.md) |

---

## 📖 How to Use This Documentation

1. **Find your role** in the "Learning Paths by Role" section above
2. **Follow the recommended path** for your role
3. **Use the search index** to find topic-specific information
4. **Each section has a dedicated README.md** with navigation links
5. **Cross-references** link related topics together

---

## 🔗 Related Resources

**Main Repository:** [github.com/mohammedakbaransari/glassbox-agentic-governance](https://github.com/mohammedakbaransari/glassbox-agentic-governance)

**Root Documentation:**
- [../../CHANGELOG.md](../../CHANGELOG.md) - Version history and changes
- [../../CONTRIBUTING.md](../../CONTRIBUTING.md) - Contribution guidelines  
- [../../README.md](../../README.md) - Project overview

**Examples:**
- [../../examples/distributed_velocity_breaker.py](../../examples/distributed_velocity_breaker.py) - Velocity breaker example
- [../../examples/industry_examples.py](../../examples/industry_examples.py) - Real-world examples

---

## 📝 For Contributors

Documentation needs your help! Consider:
- Filing [issues](https://github.com/mohammedakbaransari/glassbox-agentic-governance/issues) for unclear sections
- Submitting [PRs](https://github.com/mohammedakbaransari/glassbox-agentic-governance/pulls) with improvements
- Adding examples to existing docs
- Translating docs to other languages

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## 📞 Support

- **Documentation Issues** → File a GitHub issue
- **Questions?** → Check [USER/troubleshooting.md](USER/troubleshooting.md)
- **Feedback** → Open a discussion on GitHub
- **Security Issues** → Email security@glassbox.io (do not open public issue)

---

*GlassBox Documentation*  
*32 files | 8 categories | Fully Reorganized*

---

## 📦 Additional Resources

### [VERSIONS/](VERSIONS/) - Documentation by Version
- Find docs for your GlassBox version
- Version comparison matrix
- Migration guides
- Support timeline

### [SEARCH/](SEARCH/) - Documentation Search
- Searchable keyword index (~395 keywords)
- Search strategies and tips
- CLI search examples
- Cross-document links
