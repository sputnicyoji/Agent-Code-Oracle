# KG Schema

> Knowledge graph modeling for code contracts

## Context

Default context: `code_contracts` (configurable via `KG_CONTEXT` in `kg_injector.py`)

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
    "[involved_files] File1.ext, File2.ext",
    "[affected_external_files] External1.ext",
    "[external_consumer_count] 3",
    "[repomap_verified] Cross-module consumers verified by AST"
  ]
}
```

## Relation Types

| Relation | From | To | Meaning |
|----------|------|----|---------|
| `involves_file` | Contract entity | Filename | Contract constrains this file |
| `affects_external` | Contract entity | Filename | File outside module is affected |

## Query Patterns

```python
# By module name
aim_search_nodes(query="PaymentService")

# By filename
aim_search_nodes(query="PaymentGateway.cs")

# By contract type
aim_search_nodes(query="contract_blast_radius")
```

## Why Observations Duplicate Relations

`aim_search_nodes` only searches entity names, types, and observations -- it does NOT search relation endpoints. Filenames must exist in both:
- **observations** (for searchability)
- **relations** (for graph traversal)

Without the observations copy, filename queries return nothing.

## Entity Naming Convention

`{ModuleName}::{EnglishTitle}` -- globally unique, searchable by module prefix.
