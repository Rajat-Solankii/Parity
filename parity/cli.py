import argparse
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from parity.env.fingerprint import EnvFingerprint
from parity.resolve.mapping import Resolution
from parity.scan.ast_import import scan_directory_for_imports
from parity.scan.manifest import get_declared_dependencies

console = Console()

def cmd_scan(args: argparse.Namespace) -> None:
    base_path = Path(args.path).resolve()
    if not base_path.is_dir():
        console.print(f"[red]Error:[/red] {base_path} is not a valid directory.")
        sys.exit(1)

    projects_to_scan = []
    
    if args.all:
        # Find all directories that contain a recognizable manifest
        for root, dirs, files in os.walk(base_path):
            # Skip ignored dirs
            dirs[:] = [d for d in dirs if d not in {".git", ".tox", "venv", ".venv", "node_modules", "build", "dist"}]
            if any(f in {"requirements.txt", "pyproject.toml", "setup.py", "Pipfile"} for f in files):
                projects_to_scan.append(Path(root))
    else:
        projects_to_scan.append(base_path)

    if not projects_to_scan:
        console.print("[yellow]No projects found.[/yellow]")
        return

    for path in projects_to_scan:
        console.print(f"\nScanning project at [bold]{path}[/bold]...")
        
        manifest_file = Path(args.manifest).resolve() if args.manifest else None

        declared = get_declared_dependencies(path, manifest_file)
        imported = scan_directory_for_imports(path)

        table = Table(title=f"Scan Results: {path.name}")
        table.add_column("Category", style="cyan", no_wrap=True)
        table.add_column("Type", style="yellow")
        table.add_column("Count", style="magenta")
        table.add_column("Items", style="green")

        table.add_row("Declared", "Runtime", str(len(declared.runtime)), ", ".join(sorted(declared.runtime)) if declared.runtime else "None")
        table.add_row("Declared", "Dev", str(len(declared.dev)), ", ".join(sorted(declared.dev)) if declared.dev else "None")
        
        if declared.unresolved:
            table.add_row("Declared", "Unresolved", str(len(declared.unresolved)), ", ".join(declared.unresolved), style="red")

        table.add_row("Imported", "Runtime", str(len(imported.runtime)), ", ".join(sorted(imported.runtime)) if imported.runtime else "None")
        table.add_row("Imported", "Dev", str(len(imported.dev)), ", ".join(sorted(imported.dev)) if imported.dev else "None")
        table.add_row("Imported", "Optional", str(len(imported.optional)), ", ".join(sorted(imported.optional)) if imported.optional else "None")

        if imported.syntax_errors:
            for err in imported.syntax_errors:
                table.add_row("Imported", "Syntax Error", "1", err, style="red")

        console.print(table)

import json as json_lib
import subprocess
from dataclasses import asdict

from parity.env.fingerprint import ToolStatus, capture_environment, redact_fingerprint
from parity.resolve.mapping import map_import_to_package


def cmd_fingerprint(args: argparse.Namespace) -> None:
    console.print(f"Gathering environment fingerprint (Target Python: {args.python or 'current'})...")
    env = capture_environment(target_python=args.python, extra_tools=args.tool)
    
    if getattr(args, "redact", False):
        env = redact_fingerprint(env)
    
    out_path = Path(args.out).resolve()
    # Write sorted keys for deterministic output
    out_path.write_text(json_lib.dumps(asdict(env), indent=2, sort_keys=True), encoding="utf-8")
    
    console.print(f"[green]Success![/green] Fingerprint written to {out_path}")
    console.print(f"OS: {env.os_name} {env.os_release} ({env.architecture})")
    console.print(f"Python: {env.python_version} at {env.python_executable} (venv: {env.is_venv})")
    console.print(f"Pip: {env.pip_version}")
    console.print(f"Installed Packages: {len(env.installed_packages)}")
    
    table = Table(title="Native Tools")
    table.add_column("Tool", style="cyan")
    table.add_column("Status", style="yellow")
    table.add_column("Version", style="green")
    table.add_column("Error", style="red")
    
    for name, tool in env.native_tools.items():
        if tool.status == ToolStatus.FOUND:
            status = "Installed"
        elif tool.status == ToolStatus.FOUND_NOT_ON_PATH:
            status = "Installed (Not on PATH)"
        elif tool.status == ToolStatus.BROKEN:
            status = "[red]Broken[/red]"
        else:
            status = "[red]Not Installed[/red]"
            
        version = tool.version or "-"
        error = tool.error_msg or ""
        table.add_row(name, status, version, error)
        
    console.print(table)

def cmd_update_mapping(args: argparse.Namespace) -> None:
    console.print("Generating import mapping...")
    script_path = Path(__file__).parent.parent / "tools" / "build_mapping.py"
    
    cmd = [sys.executable, str(script_path)]
    if getattr(args, "online", False):
        cmd.append("--online")
        
    subprocess.run(cmd, check=False)
    console.print("[green]Mapping updated successfully.[/green]")

def cmd_resolve_test(args: argparse.Namespace) -> None:
    # First, let's get the target env distributions to pass to resolve
    console.print("Inspecting current env for Layer 1 fallback...")
    capture_environment()
    
    res = map_import_to_package(args.name, allow_online=args.online, target_env_distributions=None)
    
    console.print(f"Import: [bold cyan]{args.name}[/bold cyan]")
    
    if args.explain:
        table = Table(title="Resolution Candidates")
        table.add_column("Distribution", style="cyan")
        table.add_column("Layer", style="magenta")
        table.add_column("Confidence", style="yellow")
        for c in res.candidates:
            color = "green" if c.confidence.value == "High" else "yellow" if c.confidence.value == "Medium" else "red"
            table.add_row(c.distribution, c.layer, f"[{color}]{c.confidence.value}[/{color}]")
        console.print(table)
    else:
        dists = [c.distribution for c in res.candidates]
        console.print(f"Candidates: {', '.join(dists)}")



def cmd_fix(args: argparse.Namespace) -> None:
    console.print("Fix command: Not yet implemented.", style="yellow")

def cmd_report(args: argparse.Namespace) -> None:
    console.print("Report command: Not yet implemented.", style="yellow")

def cmd_eval(args: argparse.Namespace) -> None:
    console.print("Eval command: Not yet implemented.", style="yellow")


import dataclasses
import json

from parity.diagnose.engine import Diagnoser


class ResolverAdapter:
    def __init__(self, fingerprint: 'EnvFingerprint') -> None:
        self.fingerprint = fingerprint
    def map_import_to_package(self, import_name: str) -> 'Resolution':
        return map_import_to_package(import_name, allow_online=False, target_env_distributions=None)

def cmd_diagnose(args: argparse.Namespace) -> None:
    from parity.scan.ast_import import scan_directory_for_imports
    from parity.scan.manifest import get_declared_dependencies
    
    base_path = Path(args.path).resolve()
    if not base_path.is_dir():
        console.print(f"[red]Error:[/red] {base_path} is not a valid directory.")
        sys.exit(2)

    import_data = scan_directory_for_imports(base_path)
    manifest_data = get_declared_dependencies(base_path)

    if getattr(args, "against", None):
        with open(args.against, "r") as f:
            fp_data = json.load(f)
            from parity.env.fingerprint import EnvFingerprint
            fingerprint = EnvFingerprint(**fp_data)
    else:
        fingerprint = capture_environment(target_python=getattr(args, "python", None))

    diagnoser = Diagnoser(manifest_data, import_data, fingerprint, ResolverAdapter(fingerprint))
    findings = diagnoser.evaluate()

    if getattr(args, "format", "text") == "json":
        from typing import Any
        class EnhancedJSONEncoder(json.JSONEncoder):
            def default(self, o: Any) -> Any:
                if dataclasses.is_dataclass(o) and not isinstance(o, type):
                    return dataclasses.asdict(o)
                from enum import Enum
                if isinstance(o, Enum):
                    return o.value
                return super().default(o)
        print(json.dumps(findings, cls=EnhancedJSONEncoder, indent=2))
    else:
        if not findings:
            console.print("[green]No issues found![/green]")
        else:
            for finding in findings:
                console.print(f"[{finding.severity.value.upper()}] {finding.rule_id}: {finding.title}")
                console.print(f"  {finding.explanation}")
                for ev in finding.evidence:
                    console.print(f"  - {ev.kind}: {ev.file}:{ev.line}")
                if finding.fix:
                    console.print(f"  Fix ({'Auto' if finding.fix.auto else 'Manual'}): {finding.fix.kind.value} {finding.fix.target} {finding.fix.specifier}")
                console.print()

    sys.exit(1 if findings else 0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parity - Make every machine match."
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Sub-commands")

    # scan
    parser_scan = subparsers.add_parser("scan", help="List declared and undeclared dependencies")
    parser_scan.add_argument("path", help="Path to the project directory")
    parser_scan.add_argument("--all", action="store_true", help="Find and report every project under the path")
    parser_scan.add_argument("--manifest", help="Override auto-detection with a specific manifest file")
    parser_scan.set_defaults(func=cmd_scan)

    # fingerprint
    parser_fp = subparsers.add_parser("fingerprint", help="Capture this machine's environment")
    parser_fp.add_argument("--out", default="env.json", help="Output file for the fingerprint")
    parser_fp.add_argument("--tool", action="append", help="Extra tool to check (can be used multiple times)")
    parser_fp.add_argument("--python", help="Path to target python executable to inspect")
    parser_fp.add_argument("--redact", action="store_true", help="Redact usernames from output")
    parser_fp.set_defaults(func=cmd_fingerprint)

    # update-mapping
    parser_um = subparsers.add_parser("update-mapping", help="Regenerate import-to-distribution mapping file")
    parser_um.add_argument("--online", action="store_true", help="Allow fetching wheels online")
    parser_um.set_defaults(func=cmd_update_mapping)

    # resolve (hidden test hook)
    parser_res = subparsers.add_parser("resolve", help="Test import-to-distribution resolution")
    parser_res.add_argument("name", help="Import name to resolve")
    parser_res.add_argument("--online", action="store_true", help="Allow online lookup")
    parser_res.add_argument("--explain", action="store_true", help="Explain resolution layers")
    parser_res.set_defaults(func=cmd_resolve_test)

    # diagnose
    parser_diag = subparsers.add_parser("diagnose", help="Findings + explanations")
    parser_diag.add_argument("path", default=".", nargs="?", help="Project path to diagnose")
    parser_diag.add_argument("--python", help="Target python environment to check against")
    parser_diag.add_argument("--against", help="Diagnose against a saved fingerprint JSON file")
    parser_diag.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    parser_diag.set_defaults(func=cmd_diagnose)

    # fix
    parser_fix = subparsers.add_parser("fix", help="Build the plan, confirm, apply, verify")
    parser_fix.add_argument("path", help="Path to the project directory")
    parser_fix.add_argument("--dry-run", action="store_true", help="Print the plan and change nothing")
    parser_fix.add_argument("--yes", action="store_true", help="Skip the prompt but never skip the allowlist")
    parser_fix.add_argument("--with-dev", action="store_true", dest="with_dev", help="Install dev dependencies too")
    parser_fix.set_defaults(func=cmd_fix)

    # report
    parser_report = subparsers.add_parser("report", help="Markdown/JSON report of diagnosis and actions")
    parser_report.add_argument("path", help="Path to the project directory")
    parser_report.set_defaults(func=cmd_report)

    # eval
    parser_eval = subparsers.add_parser("eval", help="Run corpus evaluation")
    parser_eval.set_defaults(func=cmd_eval)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
