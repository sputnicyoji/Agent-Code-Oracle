# KG Schema

> Knowledge graph modeling for language-neutral code contracts.

## Context

Default context: `code_contracts`.

## Entity Format

```json
{
  "name": "ModuleName::ContractTitle",
  "entityType": "contract_blast_radius",
  "observations": [
    "[type] blast_radius",
    "[description] ...",
    "[blind_spot] ...",
    "[consequence] ...",
    "[confidence] 0.95",
    "[module] ModuleName",
    "[involved_files] src/module/file.ext",
    "[affected_external_files] src/consumer/file.ext",
    "[evidence_count] 2"
  ]
}
```

## Relation Types

| Relation | From | To | Meaning |
|----------|------|----|---------|
| `constrains` | Contract entity | Repo-relative path | Contract constrains this path |
| `affects_external` | Contract entity | Repo-relative path | Path outside module is affected |

## Query Patterns

```python
# By module name
aim_search_nodes(query="payment")

# By repo-relative path
aim_search_nodes(query="src/payment/result.ts")

# By filename legacy fallback
aim_search_nodes(query="result.ts")

# By contract type
aim_search_nodes(query="contract_blast_radius")
```

## Why Observations Duplicate Relations

`aim_search_nodes` searches names, types, and observations. Do not assume it searches relation endpoints.
Store paths in both observations and relations.

## Entity Naming Convention

`{ModuleName}::{EnglishTitle}`.
The module prefix is for discovery, not for project binding.
