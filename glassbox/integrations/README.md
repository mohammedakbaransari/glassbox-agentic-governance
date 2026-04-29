# glassbox/integrations

Adapters for external agent frameworks and policy systems.

## Key Modules

- `adapters.py`: LangChain/LangGraph/AutoGen style wrappers
- `extended_adapters.py`: extended adapter patterns
- `mcp_gateway.py`: MCP gateway integration support
- `opa_adapter.py`: OPA policy integration bridge

## Quick Start

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.integrations.adapters import LangChainAdapter

pipeline = GovernancePipeline()
adapter = LangChainAdapter(pipeline, agent_id="agent_lc")
# governed_tools = adapter.wrap_tools(tools)
```

## Operational Notes

- Keep payload extraction deterministic and explicit.
- Ensure framework exceptions and governance exceptions are handled cleanly.
- Verify optional external dependencies are installed for the adapter you use.

## Testing

```bash
python -m pytest tests/test_integrations.py -q
python -m pytest tests/test_api.py -q
```

## Related Docs

- [docs/API/endpoint_reference.md](../../docs/API/endpoint_reference.md)
- [docs/DEVELOPMENT/implementation_guide.md](../../docs/DEVELOPMENT/implementation_guide.md)