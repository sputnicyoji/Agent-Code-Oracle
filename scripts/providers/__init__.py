"""Graph providers for Code Oracle Stage 0.

A provider answers `get_module_external_consumers(module_name, source_root)`
and returns a list of {class_name, consumer_name, relation_type, is_external}
dicts. The shape is what pipeline.py Stage 0 expects regardless of which
provider supplied the data.

Providers shipped today:

- repomap_l3: parses Aider's RepoMap L3 markdown. Most accurate when
  available because RepoMap also gives inheritance/implements relation
  types. Lives in scripts/repomap_bridge.py for historical reasons.
- grep_fallback: zero-setup; uses git grep / rg / grep on demand to
  discover references. Slower; relation type degrades to "reference".
"""
