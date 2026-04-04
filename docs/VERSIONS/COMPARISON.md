# Version Comparison Guide

## Feature Matrix

### Core Features
| Feature | v1.0.0 | v1.0.1 | v1.1.0 |
|---------|--------|--------|--------|
| **Decision Pipeline** | ✅ 9-stage | ✅ 9-stage | ✅ 9-stage |
| **Policy Engine** | ✅ 24 policies | ✅ 24 policies | ✅ 24 policies |
| **REST API** | ✅ v1 | ✅ v1 | ✅ v1 |
| **Multi-tenancy** | ✅ Basic | ✅ Basic | ✅ TenantRegistry |
| **Audit Logging** | ✅ Basic | ✅ Enhanced | ✅ Advanced |

### Enterprise Features
| Feature | v1.0.0 | v1.0.1 | v1.1.0 |
|---------|--------|--------|--------|
| **Distributed VB** | ❌ | ✅ Lua scripts | ✅ Optimized |
| **Redis Support** | ❌ | ✅ | ✅ |
| **Circuit Breaker** | ❌ | ✅ | ✅ |
| **Decision Replay** | ❌ | ❌ | ✅ CLI-based |
| **Custom Risk Models** | ❌ | ❌ | ✅ Pluggable |
| **Workflow Chains** | ❌ | ❌ | ✅ Multi-stage |
| **Advanced Telemetry** | ❌ | Basic | ✅ Per-stage timing |

### Performance
| Metric | v1.0.0 | v1.0.1 | v1.1.0 |
|--------|--------|--------|--------|
| **Decision Latency (P99)** | ~80ms | ~40-50ms | ~30-45ms |
| **Anomaly Detection** | O(n) stats | O(1) + opt | O(1) + 95% faster |
| **Lock Contention** | Baseline | 95% reduced | 95% reduced |
| **Audit Logging** | Standard | Lock pooled | Ring buffer |
| **Throughput** | ~100 dec/s | ~400-500 dec/s | ~500-800 dec/s |

---

## API Changes

### v1.0.0 → v1.0.1
**No breaking changes** - Fully backward compatible

New classes/functions:
- `VelocityBreakerDistributed` - New distributed implementation
- `RED IS backend support
- `CircuitBreaker` - New circuit breaker pattern

### v1.0.1 → v1.1.0
**Breaking changes:** None in core API

New classes/functions:
- `TenantRegistry` - Multi-tenant isolation
- `DecisionReplay` - Historical replay
- `CustomRiskEvaluator` - Pluggable risk models
- `DecisionWorkflow` - Workflow orchestration

Deprecated (still supported):
- None yet

---

## Migration Effort

### Upgrading v1.0.0 → v1.0.1
**Difficulty:** Easy ⭐  
**Time Estimate:** 30 minutes - 2 hours  
**Breaking Changes:** None

Recommended changes:
- Add Redis for distributed VB (optional, has local fallback)
- Enable circuit breaker in new deployments
- Update monitoring for new metrics

### Upgrading v1.0.1 → v1.1.0
**Difficulty:** Easy ⭐⭐  
**Time Estimate:** 1-4 hours  
**Breaking Changes:** None in core

Recommended changes:
- No code changes required
- Optional: Leverage new enterprise features
- Update documentation and runbooks
- Consider workflow orchestration for complex cases

---

## Compatibility Matrix

### Dependencies
| Dependency | v1.0.0 | v1.0.1 | v1.1.0 | Notes |
|------------|--------|--------|--------|-------|
| Python | 3.9+ | 3.9+ | 3.9-3.12 | Official support |
| Flask | Optional | Optional | Optional | REST API only |
| Redis | N/A | Optional | Optional | Distributed VB only |
| PyYAML | Optional | Optional | Optional | Config only |

### Database Support
| DB | v1.0.0 | v1.0.1 | v1.1.0 | Notes |
|----|--------|--------|--------|-------|
| SQLite | ✅ Dev | ✅ Dev | ✅ Dev | Local development |
| PostgreSQL | ✅ Prod | ✅ Prod | ✅ Prod | Recommended |
| MySQL | ✅ Prod | ✅ Prod | ✅ Prod | Supported |
| Cloud DBs | ✅ | ✅ | ✅ | AWS RDS, GCP Cloud SQL, etc. |

---

## Documentation Updates

### Major Changes in Docs
| Doc | v1.0.0 | v1.0.1 | v1.1.0 |
|-----|--------|--------|--------|
| [CONTRIBUTING.md](../../CONTRIBUTING.md) | Initial | Improved | Current |
| [SECURITY.md](../../docs/SECURITY/hardening.md) | Basic | Enhanced | Comprehensive |
| [ARCHITECTURE.md](../../docs/DEVELOPMENT/architecture.md) | 9-stage | + VB details | + Enterprise |
| [DEPLOYMENT.md](../../docs/DEPLOYMENT/guide.md) | Basic | + Redis | + Advanced |

---

## Performance Improvements

### Latency (Single Decision)
```
v1.0.0: ████████████████████ 80ms
v1.0.1: ██████████ 40-50ms (50% improvement)
v1.1.0: ███████ 30-45ms (40-60% vs v1.0.0)
```

### Throughput (decisions/second)
```
v1.0.0: ████ ~100 dec/s
v1.0.1: ████████████ ~400-500 dec/s
v1.1.0: ██████████████████ ~500-800 dec/s
```

### Memory Usage
```
v1.0.0: Baseline
v1.0.1: ~95% lock contention reduction
v1.1.0: Ring buffer = fixed memory for audit logs
```

---

## When to Upgrade

### Upgrade v1.0.0 → v1.0.1 if you need:
- ✅ Higher throughput (4-5x improvement)
- ✅ Distributed rate limiting across instances
- ✅ Better performance in high-load scenarios
- ✅ Circuit breaker safety patterns
- ✅ Redis for fleet-level controls

### Upgrade v1.0.1 → v1.1.0 if you need:
- ✅ Multi-tenant isolation (separate customers)
- ✅ Decision replay (regression testing)
- ✅ Workflow orchestration (approval chains)
- ✅ Custom risk models (your own scoring)
- ✅ Advanced telemetry (per-stage metrics)
- ✅ Enterprise support & features

---

## Rollback Procedures

### If v1.0.1 Has Issues
1. Stop v1.0.1 deployment
2. Deploy v1.0.0 from same commit history
3. Restore database from backup
4. Verify decisions still process
5. Report issue on GitHub

### If v1.1.0 Has Issues
1. Stop v1.1.0 deployment
2. Deploy v1.0.1 from previous tag
3. Restore database from backup (if needed)
4. Verify decisions still process
5. Report issue on GitHub
6. Can upgrade back to v1.1.0 once patched

---

## Support Timelines

| Version | Released | EOL | Status |
|---------|----------|-----|--------|
| v1.0.0 | Oct 2025 | Jul 2025 | ❌ Unsupported |
| v1.0.1 | Jan 2026 | Jul 2026 | ⚠️ Extended support |
| v1.1.0 | Apr 2026 | TBD | ✅ Active support |

---

## FAQ

**Q: Do I have to upgrade?**  
A: v1.0.0 reached EOL. v1.0.1 acceptable through July 2026. Recommend v1.1.0.

**Q: Are there breaking changes?**  
A: No breaking changes in any upgrade path. All upgrades are backward compatible.

**Q: How long does upgrade take?**  
A: v1.0.0→v1.0.1: 30 min - 2 hr. v1.0.1→v1.1.0: 1-4 hours. Varies by environment.

**Q: What if I skip a version?**  
A: You can skip directly (v1.0.0 → v1.1.0) but recommended to go through each version for testing.

