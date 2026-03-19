# Prompt Templates

> 4-round LLM dialogue for contract extraction

## Round 0: L3 Cross-Module Discovery

**No LLM needed.** Parse RepoMap L3 reference graph to find cross-module consumers.

```bash
python scripts/repomap_bridge.py --l3 <l3_path> --module <name> --source-root <path>
```

Prepend output to Round 1 prompt as context:

```
## Cross-Module Consumer Data (AST-verified)

The following classes in this module are referenced by external modules:

  PaymentGateway <- OrderService (inherits)
  PaymentGateway <- InvoiceGenerator (inherits)
  EventBus <- NotificationService (inherits)

Pay special attention to these cross-module boundaries.
Each external consumer is a candidate for a blast_radius contract.
```

## Round 1: Architect's Eye

```
You are the original architect of this module. You have complete knowledge of:
- Why every design decision was made
- What implicit assumptions exist
- What breaks if someone modifies code without full context

Analyze the provided source code and answer:

1. Complete data flow topology - how does data enter, transform, and exit this module?
2. Implicit logic dependencies between components (not visible in imports)
3. Data lifetime windows - which data is only valid in certain phases?
4. Caller/callee implicit assumptions

MANDATORY QUESTION: "Who consumes this module's output?"
List ALL external files/modules that read data produced by this module.
Use the L3 cross-module data provided above as a starting point.
```

## Round 2: Contract Mining

```
Based on Round 1 analysis, extract implicit contracts as a JSON array.

Each contract:
{
  "type": "blast_radius|rationale|data_flow|ordering|thread_safety",
  "title": "English title (concise)",
  "description": "Description of the implicit knowledge",
  "blind_spot": "Why an AI agent would miss this",
  "violation_consequence": "What breaks if violated",
  "involved_files": ["File1.ext", "File2.ext"],
  "affected_external_files": ["ExternalFile.ext"],  // optional
  "confidence": 0.0-1.0
}

CONSTRAINTS:
- blast_radius + rationale types must be >= 50% of total
- Target: 15-30 contracts
- Title in English, all other text fields in your project's primary language
- involved_files: actual filenames (pipeline validates existence)
```

## Round 3: Devil's Advocate

```
Review each contract with ONE criterion:
"Could an AI agent reading all involved_files self-infer this?"

Decision matrix:
- Can infer from code alone -> DROP
- Needs files outside the module -> KEEP (add those files to involved_files)
- Needs business/historical context -> KEEP
- Borderline (could infer with effort) -> DEMOTE (lower confidence to 0.3-0.5)

Special rules (from RED Phase findings):
- thread_safety contracts about IJob vs IJobParallelFor choice -> usually DROP
- rationale contracts that are actually about concurrency patterns -> DEMOTE

Output: filtered JSON array with adjusted confidences.
Target: 15-30 contracts after filtering.
```

## L3 Data Injection Note (v4.0+)

When Round 0 provides L3 consumer data, tell the LLM in Round 1:
"The external consumer data above comes from AST static analysis and represents verified facts, not guesses. Use this data to answer 'who consumes this module's output'."
