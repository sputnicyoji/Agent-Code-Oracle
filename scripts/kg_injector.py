"""
KG Injector — Stage 5

Converts contracts to knowledge-graph MCP injection format:
- Entity: {name, entityType, observations}
- Relation: {from, to, relationType: "constrains"}

v2.0 improvements:
- observations automatically include [involved_files] and [affected_external_files]
  (fixes the issue where aim_search_nodes does not search relation endpoints)
- affected_external_files supports bidirectional queries

Output JSON format for use with aim_create_entities / aim_create_relations.
Actual injection is done by Claude calling MCP tools (Python cannot call MCP directly).
"""


from oracle_config import extract_contract_paths


class KGInjector:
    """Knowledge graph injection format converter"""

    KG_CONTEXT = "code_contracts"

    def convert(self, contracts: list[dict], module_name: str) -> dict:
        """
        Convert contracts to KG injection format

        Args:
            contracts: Contract list
            module_name: Module name

        Returns:
            {"entities": [...], "relations": [...], "context": "code_contracts"}
        """
        entities = []
        relations = []

        for c in contracts:
            entity_name = f"{module_name}::{c['title']}"
            involved = extract_contract_paths(c, "involved_files")
            affected = extract_contract_paths(c, "affected_external_files")

            # Entity observations
            observations = [
                f"[type] {c['type']}",
                f"[description] {c['description']}",
                f"[blind_spot] {c['blind_spot']}",
                f"[consequence] {c['violation_consequence']}",
                f"[confidence] {c['confidence']}",
                f"[module] {module_name}",
            ]

            # Store filenames in observations (aim_search_nodes only searches observations)
            if involved:
                observations.append(f"[involved_files] {', '.join(involved)}")
            if affected:
                observations.append(f"[affected_external_files] {', '.join(affected)}")
                observations.append(f"[external_consumer_count] {len(affected)}")
            if c.get("_l3_enriched"):
                observations.append("[repomap_verified] Cross-module consumers verified by AST")
            if c.get("evidence"):
                observations.append(f"[evidence_count] {len(c.get('evidence') or [])}")

            entities.append({
                "name": entity_name,
                "entityType": f"contract_{c['type']}",
                "observations": observations,
            })

            # Relations: contract -> module-internal files
            for f in involved:
                relations.append({
                    "from": entity_name,
                    "to": f,
                    "relationType": "constrains",
                })

            # Relations: contract -> externally affected files
            for f in affected:
                relations.append({
                    "from": entity_name,
                    "to": f,
                    "relationType": "affects_external",
                })

        return {
            "context": self.KG_CONTEXT,
            "entities": entities,
            "relations": relations,
        }
