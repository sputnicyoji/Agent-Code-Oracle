# Graph Provider Integration

> Static providers supply facts. Code Oracle adds semantic contracts.

## Provider Boundary

```text
static provider                 Code Oracle
  symbols/references              LLM semantic extraction
  changed paths                   deterministic pipeline
  impact candidates               KG persistence
       |                                 |
       v                                 v
provider adapter --------------> evidence fields
```

## Built-in Provider: RepoMap L3

RepoMap L3 is optional.
It is one provider implementation, not the core design.

```bash
python scripts/repomap_bridge.py \
  --l3 .claude/context/repomap-L3-relations.md \
  --module MyModule \
  --source-root src/MyModule/
```

## Provider Interface

Future providers should expose these concepts:

```python
get_external_consumers(symbol_or_path) -> list[Consumer]
get_changed_symbols(diff) -> list[Symbol]
get_high_impact_symbols(threshold) -> list[Symbol]
```

Use `symbol`, not `class`, in new interfaces.
A symbol may be a function, class, struct, trait, module, route, proto message, table, or field.

## Complementary Layers

| Question | Static provider provides | Code Oracle adds |
|----------|--------------------------|------------------|
| Who references X? | Structural facts | Which references are semantically risky |
| What breaks if X changes? | Candidate impact scope | Consequence and blind spot |
| Why is X designed this way? | Usually nothing | Rationale contract |
| How does data flow? | Partial references | Cross-boundary semantic flow |

## Known Provider Limits

Most static providers miss some of:

- Runtime dynamic dispatch
- Event subscriptions
- Delegate/callback chains
- Network message flows
- Configuration-to-code routing
- Reflection or code generation

These gaps belong in Round 1-3 analysis and evidence notes.
