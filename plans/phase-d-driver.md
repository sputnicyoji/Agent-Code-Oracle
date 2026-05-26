# Phase D -- Optional LLM Driver for the 4-Round Flow

> P3. High risk, high reward. The largest user-experience improvement
> available but the most opinionated. Strictly optional -- the manual flow
> documented in the skill must continue to work.

## Goal

Today, a user (or the agent acting on their behalf) runs the 4-round
extraction by hand: read module docs, call `repomap_bridge`, write Round 1
prose, write Round 2 JSON, apply Round 3 Devil's-Advocate filter, save
`round3.json`, run pipeline. On a 155-file module this took the test session
~30K tokens of intermediate reasoning to produce 13 contracts.

A `round_driver.py` orchestrates the same flow programmatically. Users still
read the skill to understand what the rounds do, but the driver removes the
"copy prompt, paste output, save file" boilerplate.

## Problems

### #9 -- No automation between Round 0 and the pipeline

Concretely:

- The agent has no way to say "scan module X with provider Y" and get a
  `round3.json` back.
- Each round's prompts live in `references/prompt-templates.md` (markdown
  text). They are not parameterised.
- Round 1's output (architecture summary) is not currently a typed artifact
  -- the agent reads it as prose and infers contracts directly. This makes
  round-to-round handoff lossy.
- For large modules, the agent's context blows up because all source files
  are read into the same conversation.

## Proposed Change

### Driver entry point

```bash
python scripts/round_driver.py \
  --config oracle.config.json \
  --module RoguelikeTower \
  --module-source client/Assets/X1/ScriptGame/RoguelikeTower \
  --module-docs docs/x15-module-document/RoguelikeTower_ForAI \
  --output round3.json \
  --provider anthropic \
  --rounds 0,1,2,3
```

### Architecture

```
round_driver.py
    |
    +-- Round0Runner       (deterministic, calls repomap_bridge)
    |     -> round0.json   {graph_evidence, internal_class_count, cross_edges, ...}
    |
    +-- Round1Runner       (LLM call, prompt from references/)
    |     input:  round0.json + module source index + module docs
    |     output: round1.json {architecture_summary, candidate_seeds}
    |
    +-- Round2Runner       (LLM call)
    |     input:  round1.json + selected source files
    |     output: round2.json {contracts_v2_draft}
    |
    +-- Round3Runner       (LLM call -- a separate persona for adversarial review)
    |     input:  round2.json
    |     output: round3.json {filtered contracts}
    |
    +-- LLMProvider abstraction
          - claude_code:      delegate to host Agent (PRIMARY for the
                              expected user -- Oracle's existing audience
                              already runs inside Claude Code)
          - anthropic:        official client (standalone scripts)
          - openai:           official client
          - local_ollama:     for offline / cost-sensitive use
          - dry_run:          print prompts but skip the call (for inspection)
```

The `claude_code` provider is the v1 priority. Oracle's current users
already operate inside Claude Code; making them paste an Anthropic API
key into a config file is a regression in setup cost. The driver runs
as a subprocess of Claude Code; the provider opens a small JSON-RPC
channel back to the host and asks the host to dispatch the call. No key
to manage, billing follows the existing session.

`anthropic` / `openai` providers come second, for CI usage and standalone
scripts where there is no host Claude Code.

### `LLMProvider` interface

```python
class LLMProvider:
    """Stateless. Each .call() is independent; no implicit conversation."""

    def call(self, system: str, user: str,
             response_format: Literal["text", "json"] = "text",
             max_tokens: int = 8000) -> str:
        raise NotImplementedError


class ClaudeCodeProvider(LLMProvider):
    """v1 PRIMARY. Delegate the LLM call back to the host Claude Code.

    The driver runs as a child of Claude Code; the user already has a
    session running with credentials and rate limits the host manages.
    Rather than ask the user to copy an API key into oracle.config.json,
    we open a small JSON-RPC channel on a Unix socket / named pipe (path
    in env var ORACLE_HOST_RPC). The host listens, accepts {system,
    user, response_format, max_tokens}, dispatches its own Anthropic call,
    returns the response text.

    When ORACLE_HOST_RPC is unset, this provider raises a clear error
    pointing at --provider anthropic as the standalone alternative.
    """
    def __init__(self, rpc_endpoint: str | None = None):
        self.rpc_endpoint = rpc_endpoint or os.environ.get("ORACLE_HOST_RPC")
        if not self.rpc_endpoint:
            raise RuntimeError(
                "claude_code provider requires ORACLE_HOST_RPC. "
                "Either run oracle from inside Claude Code (host sets this), "
                "or pass --provider anthropic / openai for standalone use."
            )

    def call(self, system, user, response_format="text", max_tokens=8000):
        # Send JSON-RPC request, await response
        ...


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str | None = None,
                 model: str = "claude-opus-4-7"):
        ...

    def call(self, system, user, response_format="text", max_tokens=8000):
        # standard messages.create call; json mode via system prompt + parsing
        ...
```

### Host-side support (Claude Code integration)

For `claude_code` provider to work, the host (Claude Code) must:

1. Set `ORACLE_HOST_RPC` to a socket / pipe path when it spawns the
   driver subprocess.
2. Listen on that socket for JSON-RPC requests.
3. Each request dispatches one LLM call via the host's normal Anthropic
   client and returns the response.

This is a small surface area on the host side (~50 LOC) and lives in the
oracle skill rather than the oracle package -- the skill knows how to
spawn driver subprocesses with the right env. Documented as the
"recommended setup" for users who already use Claude Code.

### Selecting source files for Round 2

Reading 155 files into the LLM is wasteful. Round 1 produces a
`candidate_seeds` list -- file/symbol pairs that look load-bearing. Round 2
only reads those:

```python
# Round 2 input assembly
def build_round2_input(round1: dict, module_root: Path) -> str:
    seed_files = [s["file"] for s in round1["candidate_seeds"]]
    chunks = []
    for f in seed_files:
        text = (module_root / f).read_text(encoding="utf-8", errors="ignore")
        chunks.append(f"### {f}\n```\n{text}\n```")
    return "\n\n".join(chunks)
```

Token budget per round is configurable. The driver refuses to exceed
`--max-input-tokens` (default 100k) and warns when it gets close.

### Prompts as data, not docs

`references/prompt-templates.md` currently holds prose. Convert into
parameterised templates under `templates/`:

```
templates/
    round0_evidence_pack.txt    (no LLM, just rendered for human review)
    round1_architect.txt
    round2_contract_mining.txt
    round3_devils_advocate.txt
```

Each template has typed placeholders (`{module_name}`, `{source_index}`,
`{round0_evidence}`, ...) and a small frontmatter:

```yaml
---
round: 1
model_preference: opus
max_output_tokens: 6000
response_format: text
---
```

The skill's `references/prompt-templates.md` becomes a documentation index
pointing at the templates -- so manual users still see the prompts, but the
driver loads them programmatically.

## Files Touched / Added

| File | Change |
|------|--------|
| `scripts/round_driver.py` | New entry point |
| `scripts/providers/__init__.py` | LLMProvider abstract base |
| `scripts/providers/claude_code_provider.py` | v1 primary: JSON-RPC back to host |
| `scripts/providers/anthropic_provider.py` | Anthropic implementation |
| `scripts/providers/openai_provider.py` | OpenAI implementation |
| `scripts/providers/ollama_provider.py` | Local Ollama implementation |
| `scripts/providers/dry_run_provider.py` | Print-only provider |
| `code-oracle/SKILL.md` (skill side) | Document spawning driver with `ORACLE_HOST_RPC` set |
| `scripts/round_artifacts.py` | Typed schemas for round0/1/2/3 JSON |
| `templates/round*.txt` | New parameterised prompts |
| `references/prompt-templates.md` | Becomes index of `templates/` |
| `pyproject.toml` | New optional deps: `anthropic`, `openai`, `httpx` (extras) |
| `README.md` | "Manual flow" vs "Driver flow" section |
| `tests/test_round_driver.py` | Tests using `DryRunProvider` |
| `tests/test_providers.py` | Provider contract tests |

## Verification

```bash
# Without API keys: driver still runnable in dry-run mode
python scripts/round_driver.py \
  --config oracle.config.json \
  --module RoguelikeTower \
  --module-source ... \
  --provider dry_run \
  --output /tmp/rt-round3.json

# Output: prints the rendered prompts for each round, writes a stub
# round3.json with the same shape an LLM would produce. Lets users
# review what would be sent before paying for inference.

# With Anthropic key
ANTHROPIC_API_KEY=... python scripts/round_driver.py \
  --provider anthropic --model claude-opus-4-7 ...

# The output round3.json should pass:
python scripts/pipeline.py --config oracle.config.json \
  --input /tmp/rt-round3.json --module-name RoguelikeTower \
  --source-root ... --output /tmp/rt-out.json
# Quality gate result should match the manual scan
```

## Risk / Migration

- **Largest scope of any phase.** Implement as a separate package
  (`scripts/providers/`) and keep `pipeline.py` provider-agnostic.
- **Optional deps.** `anthropic`, `openai`, `httpx` belong in
  `[project.optional-dependencies]` so the base install stays zero-deps.
  `requirements.txt` style `pip install agent-code-oracle[anthropic]`.
- **Cost surprise.** A 155-file module Round 2 can burn 50K input tokens.
  The driver prints estimated cost before each round and supports
  `--confirm-before-each-round` for review.
- **Reproducibility.** Each round's input is hashed and the LLM response
  cached under `.oracle-cache/<round-hash>.json`. Re-runs are free unless
  inputs change.
- **Skill compatibility.** The manual flow described in `code-oracle/SKILL.md`
  remains the source of truth. The driver is documented as an opt-in
  shortcut. Skill must not assume driver presence.

## Out of Scope for Phase D

- Built-in scan UI / web dashboard.
- Multi-module orchestration (scan project = N modules in one command).
  Could be Phase G.
- Auto-rerun on file changes ("oracle watch"). Niche.
- Fine-tuned local models. Local Ollama with off-the-shelf models is enough
  for v1.
