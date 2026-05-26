"""Dry-run LLM provider.

Prints the rendered prompt with a banner instead of calling any model.
Returns a deterministic stub string so the driver can be exercised
end-to-end without API access.

The stub format is designed to be valid JSON when response_format is
"json", so Round 2/3 stages downstream of the driver do not crash on
JSON parse errors during a dry-run.

This provider exists for three audiences:

1. Users who want to review what the driver would send before they pay
   for inference -- run with --provider dry_run, read the printed
   prompts, then re-run with --provider claude_code (or anthropic).
2. CI tests that need an LLMProvider implementation but no network.
3. Developers iterating on prompts -- edit templates/, run dry_run,
   eyeball the rendered text, repeat.
"""

from __future__ import annotations

import sys

from providers.base import LLMProvider, ResponseFormat


# Stubs keyed by a "round" kwarg the driver passes. The driver labels
# each call so DryRun can return shape-appropriate stubs without parsing
# the prompts.
_STUB_RESPONSES = {
    "round1": (
        "[DryRun Round 1 stub] "
        "Architecture analysis would go here. Replace with a real provider "
        "to extract actual data flow / consumer / lifecycle observations."
    ),
    "round2": (
        # Valid JSON array; one minimal contract so the pipeline downstream
        # has something to chew on without LLM-produced content.
        '[\n'
        '  {\n'
        '    "schema_version": 2,\n'
        '    "type": "rationale",\n'
        '    "title": "DryRun stub contract -- replace with real provider",\n'
        '    "description": "DryRun fills this slot. Use a real provider to extract genuine contracts.",\n'
        '    "blind_spot": "DryRun produces no real contracts -- it is a developer tool, not a scan.",\n'
        '    "violation_consequence": "Treating DryRun output as a real scan ships meaningless contracts.",\n'
        '    "involved": [{"path": "DRY_RUN_PLACEHOLDER"}],\n'
        '    "evidence": [{"kind": "design_rationale", "source": "doc", "target": "DryRunProvider docs"}],\n'
        '    "confidence": 0.1\n'
        '  }\n'
        ']\n'
    ),
    "round3": (
        # Round 3 filtered output: empty array is a meaningful "Devil's
        # Advocate dropped every candidate" signal. DryRun returns it so
        # the driver's output isn't a sea of fake contracts.
        "[]\n"
    ),
}


class DryRunProvider(LLMProvider):
    """LLMProvider that does not call any model."""

    def __init__(self, *, banner: bool = True, stream: object | None = None):
        """
        Args:
            banner: when True (default), print a visible header before
                each rendered prompt so DryRun output stands out.
            stream: file-like object to write to; defaults to stderr so
                stdout stays available for piping the structured output.
        """
        self.banner = banner
        self.stream = stream or sys.stderr

    def call(
        self,
        system: str,
        user: str,
        response_format: ResponseFormat = "text",
        max_tokens: int = 8000,
        **kwargs,
    ) -> str:
        round_name = str(kwargs.get("round", "unknown"))
        if self.banner:
            sep = "=" * 72
            print(f"\n{sep}", file=self.stream)
            print(f"[DryRun] LLM call ({round_name}, format={response_format}, "
                  f"max_tokens={max_tokens})", file=self.stream)
            print(sep, file=self.stream)
            if system.strip():
                print("--- system prompt ---", file=self.stream)
                print(system, file=self.stream)
            print("--- user prompt ---", file=self.stream)
            print(user, file=self.stream)
            print(sep + "\n", file=self.stream)
        return _STUB_RESPONSES.get(round_name, "[DryRun] (no stub for this round)")
