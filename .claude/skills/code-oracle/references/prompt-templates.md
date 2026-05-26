# Prompt Templates

> Language-neutral four-round dialogue for implicit contract extraction.

## Round 0: Static Provider Discovery

Use a configured graph provider when available.
The built-in provider is RepoMap L3:

```bash
python scripts/repomap_bridge.py --l3 <l3_path> --module <name> --source-root <path>
```

Prepend provider output to Round 1 as evidence:

```text
## External Consumer Evidence (static-provider verified)

The following module symbols are referenced by external symbols:

  src/payment/result.ts#PaymentResult <- src/invoice/generator.ts#createInvoice
  src/payment/result.ts#PaymentResult <- src/audit/events.ts#recordPayment

Use this as evidence for possible blast_radius contracts.
Do not treat structural references as semantic consequences by themselves.
```

## Round 1: Architect's Eye

```text
You are analyzing this module as its original architect.
You know the design intent, implicit assumptions, and downstream breakage risk.

Analyze the provided source and static-provider evidence.
Answer:

1. How does data enter, transform, and leave this module?
2. Which assumptions are not local to one file?
3. Which data shapes or states have validity windows?
4. Who consumes this module's output?
5. Which consequences cannot be inferred from direct imports/calls alone?

Use repo-relative paths.
Use symbols only when they clarify the path-level contract.
```

## Round 2: Contract Mining

```text
Extract implicit contracts as a JSON array.

Preferred schema:
{
  "schema_version": 2,
  "type": "blast_radius|rationale|data_flow|ordering|thread_safety",
  "title": "English title",
  "description": "Project primary language description",
  "blind_spot": "Why an AI agent would miss this",
  "violation_consequence": "What breaks if violated",
  "scope": {"module": "<module>", "language": "<language-if-known>"},
  "involved": [{"path": "repo/relative/path.ext", "symbols": ["OptionalSymbol"]}],
  "affected_external": [{"path": "repo/relative/consumer.ext", "symbols": ["OptionalConsumer"]}],
  "evidence": [{"kind": "static_reference|design_rationale|data_flow_trace", "source": "<source>", "target": "<path-or-symbol>"}],
  "confidence": 0.0-1.0
}

Constraints:
- blast_radius + rationale should meet the configured high-value ratio.
- Target 15-30 contracts for large modules, fewer for small modules.
- Use repo-relative paths, not basenames.
- blast_radius contracts require source-backed evidence.
- Do not emit language-specific fields unless they are metadata under scope.
```

## Round 3: Devil's Advocate

```text
Review each contract with one criterion:
Could an AI agent infer this by reading all involved paths?

Decision matrix:
- Can infer from local code alone -> DROP
- Needs external consumers -> KEEP and include affected_external/evidence
- Needs business/design/history context -> KEEP and include evidence/source
- Borderline -> DEMOTE confidence to 0.3-0.5

Special rules:
- Direct type/API constraints are usually DROP.
- Pure ordering visible in one file is usually DROP.
- Thread-safety visible from obvious locks/async/thread annotations is usually DEMOTE or DROP.

Output filtered JSON only.
```
