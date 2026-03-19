# Quick Start: Python Projects

> Get Code Oracle running on a Python project in under 15 minutes.

---

## Prerequisites

- Python 3.10+
- Claude Code installed and authenticated
- Git repository

Optional:
- Ollama with `bge-m3` (better dedup)
- Aider (for RepoMap L3 enrichment)

---

## Step 1: Install

```bash
# Copy into your project
cp -r /path/to/Agent-Code-Oracle/.claude/skills/code-oracle/ .claude/skills/
mkdir -p scripts
cp /path/to/Agent-Code-Oracle/scripts/*.py scripts/

# No pip install needed — zero external dependencies in base mode
```

---

## Step 2: Configure

Create `oracle.config.json` in your project root:

```json
{
  "project_name": "MyPythonProject",
  "file_extension": ".py",
  "scanned_modules": {
    "PaymentService": {
      "source_root": "src/payment/",
      "contract_output": "docs/contracts/payment.json"
    },
    "UserAuth": {
      "source_root": "src/auth/",
      "contract_output": "docs/contracts/auth.json"
    }
  },
  "repomap_l3": ".claude/context/repomap-L3-relations.md",
  "sync_report": ".claude/context/oracle-sync-report.json",
  "embedding": {
    "enabled": false,
    "model": "bge-m3",
    "ollama_url": "http://localhost:11434"
  }
}
```

---

## Step 3: Scan a Module

Inside Claude Code, run:

```
/code-oracle scan src/payment/
```

This triggers the 4-round extraction. The skill will:
1. Survey your module structure (Round 0)
2. Identify architectural boundaries (Round 1)
3. Extract contracts with confidence scores (Round 2)
4. Challenge and filter contracts (Round 3)
5. Save output as `round3-output.json`

Expect 3–8 minutes for a 20–50 file module.

---

## Step 4: Run the Pipeline

```bash
python scripts/pipeline.py \
  --input round3-output.json \
  --module-name PaymentService \
  --source-root src/payment/ \
  --output docs/contracts/payment.json
```

The pipeline will print a quality gate summary:

```
=== Code Oracle Pipeline ===
Module: PaymentService
Input: 22 contracts

[1/5] Contract Validation...
  [OK] 21/22 passed

[2/5] Semantic Dedup...
  [OK] 18 unique contracts

[3/5] Blind Spot Filter...
  [DEMOTE] obvious_type_constraint: "PaymentRequest fields must not be None"

[4/5] Stats + Quality Gate...
  Total: 17, Effective (conf > 0.5): 15
  Avg confidence: 0.847 (effective: 0.871)
  P0+P1 ratio: 58.8% [PASS]

[5/5] KG Injection Format...
  [OK] 17 entities, 34 relations

=== Pipeline Complete ===
```

---

## Step 5: Inject into Knowledge Graph

```
/code-oracle inject docs/contracts/payment.json
```

Or manually: copy the `kg_format` section from the output and call your KG tool.

---

## Step 6: Query Before Modifying

The next time you work on `PaymentService`, query first:

```
/code-oracle query PaymentProcessor.py
```

Or directly in your KG tool:
```
search_nodes(context="code_contracts", query="PaymentProcessor")
```

---

## Install Git Hook

```bash
cp /path/to/Agent-Code-Oracle/hooks/post-merge .git/hooks/post-merge
chmod +x .git/hooks/post-merge
```

After each `git merge`, stale contracts will be flagged automatically.

---

## Enable Semantic Dedup (Optional)

```bash
# Install Ollama: https://ollama.ai
ollama pull bge-m3
```

Then set `"enabled": true` in `oracle.config.json` under `embedding`.
Re-run the pipeline — dedup accuracy improves for semantically similar contracts
with different wording.

---

## Troubleshooting

**Pipeline reports "File not found" for involved_files**

The validator checks that files listed in contracts actually exist in `source_root`.
If your source layout changed since extraction, re-scan or manually update the contract.

**P0+P1 ratio below 50%**

Round 3 may have been too aggressive. Try re-running the Devil's Advocate round with
a less strict prompt, or manually review the filtered contracts (tagged with `_filter_tag`).

**Quality gate: effective < 10**

Too many low-confidence contracts survived. Consider raising the confidence floor in
Round 2 prompting, or reducing module scope (scan one subdirectory at a time).
