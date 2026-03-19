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
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            print(f"[Oracle Sync] Loaded config from: {path}")
            return cfg

    # No config found: return empty defaults so the tool degrades gracefully
    print("[Oracle Sync] Warning: oracle.config.json not found. "
          "Create one from oracle.config.json.example to specify module paths.", file=sys.stderr)
    return {"contract_paths": [], "scanned_modules": {}}


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


def get_changed_cs_files(commits: int = 1) -> list[str]:
    """Get .cs files changed in recent merge"""
    try:
        root = find_project_root()
        result = subprocess.run(
            ["git", "diff", "--name-only", "ORIG_HEAD", "HEAD"],
            capture_output=True, text=True, cwd=str(root)
        )
        if result.returncode != 0:
            # Fallback to HEAD~N
            result = subprocess.run(
                ["git", "diff", "--name-only", f"HEAD~{commits}"],
                capture_output=True, text=True, cwd=str(root)
            )
        return [Path(f).name for f in result.stdout.splitlines() if f.endswith(".cs")]
    except Exception:
        return []


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

    # 1. Get changed files
    changed = get_changed_cs_files()
    if not changed:
        print("[Oracle Sync] No .cs files changed, skipping")
        return

    # 2. Load existing contracts
    contracts = load_all_contracts(project_root, contract_paths)
    if not contracts:
        print("[Oracle Sync] No existing contracts found, skipping")
        return

    # 3. Run incremental scanner
    from incremental_scanner import IncrementalScanner
    scanner = IncrementalScanner(l3_path, None)
    scanner.contracts = contracts
    scan_result = scanner.analyze(changed)

    # 4. Run freshness checker
    from freshness_checker import FreshnessChecker
    fresh_results = []
    for module_name, rel_path in scanned_modules.items():
        source_root = str(project_root / rel_path)
        if os.path.isdir(source_root):
            checker = FreshnessChecker(source_root)
            module_contracts = [c for c in contracts
                                if c.get("title", "").startswith(f"{module_name}::") or True]
            fresh_results.extend(checker.check(module_contracts))

    # 5. Build report
    stale = [r for r in fresh_results if r["status"] == "STALE"]
    missing = [r for r in fresh_results if r["status"] == "MISSING_ALL"]
    report = {
        "timestamp": datetime.now().isoformat(),
        "trigger": "post-merge",
        "changed_files_count": len(changed),
        "stale_contracts": [
            {"title": r["title"], "type": r["type"], "missing_files": r["missing_files"]}
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
            f"{scan_result['summary']['contracts_affected']} affected / "
            f"{scan_result['summary']['uncovered']} uncovered"
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
    """Send Windows toast notification"""
    try:
        ps_script = (
            f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, '
            f'ContentType = WindowsRuntime] | Out-Null; '
            f'$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(0); '
            f'$text = $xml.GetElementsByTagName("text"); '
            f'$text[0].AppendChild($xml.CreateTextNode("Code Oracle Sync")) | Out-Null; '
            f'$text[1].AppendChild($xml.CreateTextNode("{summary}")) | Out-Null; '
            f'$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); '
            f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Code Oracle").Show($toast)'
        )
        subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, timeout=5
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
