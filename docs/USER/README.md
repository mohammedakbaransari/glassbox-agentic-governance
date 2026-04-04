# User Guides & Tutorials

This directory contains user-focused documentation including quick start guides, real-world use cases, and troubleshooting.

## 📖 Contents

### Getting Started
- **[quick_start.md](quick_start.md)** (5 min read)
  - Installation & setup
  - First decision evaluation
  - Basic configuration
  - Verification steps

### Use Cases & Examples
- **[use_cases.md](use_cases.md)**
  - Real-world scenario: Financial decisions
  - Scenario: E-commerce transactions
  - Scenario: Healthcare recommendations
  - Scenario: IT operations automation
  - Scenario: HR processes

### Troubleshooting
- **[troubleshooting.md](troubleshooting.md)**
  - Common issues and solutions
  - Performance tuning
  - Log analysis
  - Getting help

## 🚀 Getting Started Path

1. **Start here**: [quick_start.md](quick_start.md) - 5 minutes
2. **Explore examples**: [use_cases.md](use_cases.md) - 10 minutes
3. **Understand concepts**: [../DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md) - 15 minutes
4. **Check scenarios**: [../FEATURES/](../FEATURES/) - 10 minutes
5. **Deploy safely**: [../DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md) - varies

## 🎯 By Role

### System Administrator
1. Read [quick_start.md](quick_start.md)
2. Review [../DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
3. Check [../SECURITY/hardening.md](../SECURITY/hardening.md)
4. Monitor with [../DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)

### Business Analyst
1. Understand [use_cases.md](use_cases.md)
2. Review [../COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)
3. Check [../DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md) for decision flow
4. Explore audit features in [../FEATURES/](../FEATURES/)

### Developer/Integrator
1. Read [quick_start.md](quick_start.md)
2. Deep dive: [../DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md)
3. API reference: [../API/endpoint_reference.md](../API/endpoint_reference.md)
4. Implementation: [../DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md)

### Operations Team
1. Deployment: [../DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md)
2. Performance: [../DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)
3. Security: [../SECURITY/hardening.md](../SECURITY/hardening.md)
4. Issues: [troubleshooting.md](troubleshooting.md)

## ❓ I Need Help With...

| Need | See |
|------|-----|
| Getting started | [quick_start.md](quick_start.md) |
| Understanding the system | [../DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md) |
| Real-world examples | [use_cases.md](use_cases.md) |
| Something isn't working | [troubleshooting.md](troubleshooting.md) |
| API integration | [../API/endpoint_reference.md](../API/endpoint_reference.md) |
| Production deployment | [../DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md) |
| Security concerns | [../SECURITY/hardening.md](../SECURITY/hardening.md) |
| Performance issues | [../DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md) |
| Compliance questions | [../COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md) |

## 📚 Key Concepts

### Decisions
Every action through GlassBox is evaluated as a **Decision** with:
- Agent ID (who's making it)
- Decision type (what kind)
- Payload (the details)
- Context (surrounding conditions)

### Policies
**Policies** are business rules that evaluate decisions against:
- Regulatory requirements
- Business logic
- Risk thresholds
- Compliance rules

### Disposition
Every decision results in a **Disposition**:
- ✅ **AUTO_EXECUTE** - Approved, proceed immediately
- ⚠️ **HUMAN_REVIEW** - Needs human approval first
- 🚫 **BLOCK** - Rejected, cannot proceed

## 💡 Best Practices

1. **Start small** - Test with non-critical decisions first
2. **Monitor carefully** - Watch audit logs and metrics
3. **Gradual rollout** - Increase traffic/scope slowly
4. **Document policies** - Keep policy rules documented
5. **Review regularly** - Check for false positives/negatives
6. **Have backups** - Maintain failover policies
7. **Test performance** - Simulate production load
8. **Security first** - Follow hardening guide
9. **Compliance aware** - Maintain audit trail
10. **Stay updated** - Monitor release notes

## 🔗 Related Sections

- **For developers**: [../DEVELOPMENT/](../DEVELOPMENT/)
- **For operations**: [../DEPLOYMENT/](../DEPLOYMENT/)
- **For security**: [../SECURITY/](../SECURITY/)
- **For compliance**: [../COMPLIANCE/](../COMPLIANCE/)
- **API reference**: [../API/](../API/)

## 📬 Support

If you can't find what you need:
1. Check [troubleshooting.md](troubleshooting.md)
2. Review [../GLOSSARY.md](../glossary.md) for terminology
3. Search the documentation
4. Check GitHub issues
5. Contact support team


