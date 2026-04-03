# glassbox/integrations — AI Framework Adapters

The `integrations` package provides native adapters for popular AI frameworks.

| Module | Role |
|---|---|
| `adapters.py` | `LangChainAdapter`, `LangGraphAdapter`, `AutoGenAdapter`, `GenericToolAdapter` |

**Every tool call / graph node / function is automatically governed — transparent to the AI framework.**

```python
# LangChain
adapter = LangChainAdapter(pipeline, agent_id="my_agent")
governed_tools = adapter.wrap_tools([tool1, tool2])

# LangGraph
adapter = LangGraphAdapter(pipeline)
governed_node = adapter.wrap_node(my_fn, "agent_id", DecisionType.PROCUREMENT,
                                   payload_extractor=lambda s: {"amount": s["amount"]})

# AutoGen
adapter = AutoGenAdapter(pipeline, agent_id="autogen_agent")
governed_map = adapter.govern_function_map({"place_order": place_order_fn})

# Any callable
adapter = GenericToolAdapter(pipeline)
@adapter.govern("my_agent", DecisionType.FINANCIAL)
def transfer_funds(amount, account): ...
```

See [../../docs/USECASES.md](../../docs/USECASES.md) Patterns 7 and 8.
