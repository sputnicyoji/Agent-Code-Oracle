"""
Oracle Sync -- Post-merge contract sync orchestrator

Triggered by git post-merge hook when scanned module files change.
Runs incremental_scanner + freshness_checker, writes report, sends Win notification.

Configuration is loaded from oracle.config.json (next to this script or project root).
See oracle.config.json.example for the expected format.

CLI:
    python oracle_sync.py --l3 <l3_path> --report <report_path>
    python oracle_sync.py --l3 <l3_path> --report <report_path> --notify
    python oracle_sync.py --config ./oracle.config.json --report <report_path>
"""

import json
import os
import subprocess
import sys
import argparse
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

from oracle_config import (
    is_included,
    load_json_config,
    normalize_config,
)


def load_config(config_path: str = None) -> dict:
    """Load oracle.config.json from provided path or auto-detect locations.

    Config format:
    {
        "contract_paths": [
            "docs/module-document/ModuleA/pipeline-output.json",
            "docs/module-document/ModuleB/pipeline-output.json"
        ],
        "scanned_modules": {
            "ModuleA": "src/ModuleA/",
            "ModuleB": "src/ModuleB/"
        }
    }
    """
    search_paths = []
    if config_path:
        search_paths.append(Path(config_path))
    # Auto-detect: next to this script, then project root
    search_paths.append(SCRIPT_DIR / "oracle.config.json")
    search_paths.append(SCRIPT_DIR.parent / "oracle.config.json")

    for path in search_paths:
        if path.exists():
            cfg = normalize_config(load_json_config(path))
            print(f"[Oracle Sync] Loaded config from: {path}")
            return cfg

    # No config found: return empty defaults so the tool degrades gracefully
    print("[Oracle Sync] Warning: oracle.config.json not found. "
          "Create one from oracle.config.json.example to specify module paths.", file=sys.stderr)
    return normalize_config({})


def find_project_root() -> Path:
    """Find project root via git"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return Path(__file__).resolve().parent.parent.parent.parent.parent


def get_changed_files(commits: int = 1, include: list[str] | None = None, exclude: list[str] | None = None) -> list[str] | None:
    """Get changed files in recent merge using configured globs.

    Returns:
        list[str] -- changed paths (possibly empty -> truly no changes)
        None      -- git failed (different from "no changes"); callers
                     should not treat this as "nothing to do".
    """
    if commits < 1:
        # Negative / zero commits would build malformed git revisions like HEAD~-1.
        print(f"[Oracle Sync] Warning: invalid --commits {commits}, using 1", file=sys.stderr)
        commits = 1
    try:
        root = find_project_root()
        result = subprocess.run(
            ["git", "diff", "--name-only", "ORIG_HEAD", "HEAD"],
            capture_output=True, text=True, cwd=str(root), timeout=30,
        )
        if result.returncode != 0:
            # Fallback to HEAD~N (e.g. first merge in shallow clone)
            result = subprocess.run(
                ["git", "diff", "--name-only", f"HEAD~{commits}"],
                capture_output=True, text=True, cwd=str(root), timeout=30,
            )
            if result.returncode != 0:
                # Both forms failed -- this is a real error, not "no changes".
                print(
                    f"[Oracle Sync] git diff failed (rc={result.returncode}): "
                    f"{result.stderr.strip()[:200]}",
                    file=sys.stderr,
                )
                return None
        return [
            f.replace("\\", "/")
            for f in result.stdout.splitlines()
            if is_included(f, include or ["**/*"], exclude or [])
        ]
    except subprocess.TimeoutExpired:
        print("[Oracle Sync] git diff timed out", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"[Oracle Sync] git diff error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def load_all_contracts(project_root: Path, contract_paths: list[str]) -> list[dict]:
    """Load contracts from all scanned module outputs"""
    all_contracts = []
    for rel_path in contract_paths:
        full_path = project_root / rel_path
        if full_path.exists():
            with open(full_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            contracts = data.get("contracts", data) if isinstance(data, dict) else data
            if isinstance(contracts, list):
                all_contracts.extend(contracts)
    return all_contracts


def run_sync(l3_path: str, report_path: str, notify: bool = False, config_path: str = None):
    """Run full sync pipeline"""
    project_root = find_project_root()
    cfg = load_config(config_path)
    contract_paths = cfg.get("contract_paths", [])
    scanned_modules = cfg.get("scanned_modules", {})
    if not l3_path:
        provider = cfg.get("graph_provider") or {}
        if provider.get("type") == "repomap_l3":
            l3_path = provider.get("path")

    # 1. Get changed files. None != [] -- None means git failed, abort.
    changed = get_changed_files(
        include=cfg.get("include"),
        exclude=cfg.get("exclude"),
    )
    if changed is None:
        print("[Oracle Sync] Aborting: could not determine changed files", file=sys.stderr)
        return
    if not changed:
        print("[Oracle Sync] No matching files changed, skipping")
        return

    # 2. Load existing contracts
    contracts = load_all_contracts(project_root, contract_paths)
    if not contracts:
        print("[Oracle Sync] No existing contracts found, skipping")
        return

    # 3. Run incremental scanner. Guard against malformed contracts in any
    # loaded module file -- a single bad dict should not lose the whole report.
    from incremental_scanner import IncrementalScanner
    scanner = IncrementalScanner(l3_path, None, cfg.get("include"), cfg.get("exclude"))
    scanner.contracts = contracts
    try:
        scan_result = scanner.analyze(changed)
    except Exception as exc:
        print(
            f"[Oracle Sync] Scanner failed ({type(exc).__name__}: {exc}); "
            f"emitting freshness-only report",
            file=sys.stderr,
        )
        scan_result = {
            "affected_contracts": [],
            "impact_chain": [],
            "summary": {"contracts_affected": 0, "uncovered": 0},
        }

    # 4. Run freshness checker
    from freshness_checker import FreshnessChecker
    fresh_results = []
    for module_name, module_cfg in scanned_modules.items():
        if isinstance(module_cfg, dict):
            rel_path = module_cfg.get("source_root")
        else:
            rel_path = module_cfg
        if not rel_path:
            continue
        source_root = str(project_root / rel_path)
        if os.path.isdir(source_root):
            checker = FreshnessChecker(
                source_root,
                extended_root=str(project_root),
                include=cfg.get("include"),
                exclude=cfg.get("exclude"),
            )
            module_contracts = [c for c in contracts
                                if c.get("scope", {}).get("module") == module_name
                                or c.get("module") == module_name
                                or c.get("title", "").startswith(f"{module_name}::")]
            fresh_results.extend(checker.check(module_contracts))

    # 5. Build report
    stale = [r for r in fresh_results if r["status"] == "STALE"]
    missing = [r for r in fresh_results if r["status"] == "MISSING_ALL"]
    scan_summary = scan_result.get("summary", {}) or {}
    report = {
        "timestamp": datetime.now().isoformat(),
        "trigger": "post-merge",
        "changed_files_count": len(changed),
        "stale_contracts": [
            {
                "title": r["title"],
                "type": r["type"],
                "missing_files": r.get("missing_files", []),
                # Preserve hash-change reason so the report explains *why* a
                # contract is stale when no involved file is missing.
                "hash_changed": r.get("hash_changed", []),
            }
            for r in stale
        ],
        "missing_contracts": [
            {"title": r["title"], "type": r["type"]}
            for r in missing
        ],
        "affected_contracts": scan_result.get("affected_contracts", []),
        "uncovered_high_impact": [
            ic for ic in scan_result.get("impact_chain", [])
            if ic.get("consumer_count", 0) >= 3
        ],
        "summary": (
            f"{len(stale)} STALE / {len(missing)} MISSING / "
            f"{scan_summary.get('contracts_affected', 0)} affected / "
            f"{scan_summary.get('uncovered', 0)} uncovered"
        ),
    }

    # 6. Write report
    report_file = Path(report_path)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"[Oracle Sync] Report: {report['summary']}")
    print(f"[Oracle Sync] Saved to: {report_path}")

    # 7. Windows notification
    if notify:
        send_notification(report["summary"])


def send_notification(summary: str):
    """Send Windows toast notification.

    The summary text is passed via the CODE_ORACLE_MSG environment variable,
    NOT interpolated into the PowerShell source. Interpolating user-controlled
    strings into a `-Command` payload is a code-execution vulnerability: any
    contract title containing `");` could break out of the string and run
    arbitrary PowerShell. Environment variables are not parsed by the
    PowerShell language at all.
    """
    try:
        ps_script = (
            '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, '
            'ContentType = WindowsRuntime] | Out-Null; '
            '$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(0); '
            '$text = $xml.GetElementsByTagName("text"); '
            '$text[0].AppendChild($xml.CreateTextNode("Code Oracle Sync")) | Out-Null; '
            '$text[1].AppendChild($xml.CreateTextNode($env:CODE_ORACLE_MSG)) | Out-Null; '
            '$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); '
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Code Oracle").Show($toast)'
        )
        env = os.environ.copy()
        env["CODE_ORACLE_MSG"] = summary
        subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, timeout=5, env=env,
        )
    except Exception:
        # Fallback: simple console notification
        print(f"[Oracle Sync] NOTIFICATION: {summary}")


def main():
    parser = argparse.ArgumentParser(description="Oracle Sync - Post-merge contract sync")
    parser.add_argument("--l3", help="Path to repomap-L3-relations.md")
    parser.add_argument("--report", required=True, help="Output report JSON path")
    parser.add_argument("--notify", action="store_true", help="Send Windows toast notification")
    parser.add_argument("--commits", type=int, default=1, help="Fallback commits to diff")
    parser.add_argument("--config", help="Path to oracle.config.json (default: auto-detect)")

    args = parser.parse_args()
    run_sync(args.l3, args.report, args.notify, args.config)


if __name__ == "__main__":
    main()
