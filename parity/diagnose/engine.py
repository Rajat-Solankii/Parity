import json
from pathlib import Path
from typing import Any, Protocol

import packaging.markers
import packaging.requirements
import packaging.version
from packaging.utils import canonicalize_name

from parity.diagnose.models import (
    Confidence,
    Evidence,
    Finding,
    FixAction,
    FixKind,
    Group,
    Severity,
)
from parity.env.fingerprint import EnvFingerprint
from parity.resolve.mapping import Resolution
from parity.scan.ast_import import ImportData
from parity.scan.manifest import Location, ManifestData


class ResolverInterface(Protocol):
    def map_import_to_package(self, import_name: str) -> Resolution: ...

def load_rules_catalog() -> dict[str, Any]:
    path = Path(__file__).parent / "rules_catalog.json"
    with open(path, "r", encoding="utf-8") as f:
        return dict(json.load(f))

class Diagnoser:
    def __init__(self, manifest: ManifestData, imports: ImportData, fingerprint: EnvFingerprint, resolver: ResolverInterface):
        self.manifest = manifest
        self.imports = imports
        self.fingerprint = fingerprint
        self.resolver = resolver
        self.findings: list[Finding] = []
        self.catalog = load_rules_catalog()
        
        # Build environment for marker evaluation
        # We need sys_platform, platform_machine, python_version, etc.
        # Fingerprint doesn't directly have all PEP 508 marker vars, but we can fake/approximate what we have.
        # Parity fingerprint: python_version, os_name, architecture
        self.marker_env = {
            "python_version": self.fingerprint.python_version or "3.0", # default fallback
            "python_full_version": self.fingerprint.python_version or "3.0.0",
            "os_name": "nt" if "windows" in (self.fingerprint.os_name or "").lower() else "posix",
            "sys_platform": "win32" if "windows" in (self.fingerprint.os_name or "").lower() else ("darwin" if "mac" in (self.fingerprint.os_name or "").lower() else "linux"),
            "platform_machine": self.fingerprint.architecture or "x86_64",
            "platform_system": "Windows" if "windows" in (self.fingerprint.os_name or "").lower() else "Linux",
            "implementation_name": "cpython",
        }

    def _get_severity(self, rule_id: str) -> Severity:
        sev_str = self.catalog.get(rule_id, {}).get("default_severity", "medium").upper()
        return getattr(Severity, sev_str) # type: ignore

    def _format_msg(self, rule_id: str, **kwargs: Any) -> str:
        tpl = self.catalog.get(rule_id, {}).get("explanation_template", "")
        return str(tpl.format(**kwargs))

    def _title(self, rule_id: str) -> str:
        return str(self.catalog.get(rule_id, {}).get("title", rule_id))

    def evaluate(self) -> list[Finding]:
        self._evaluate_r1()
        self._evaluate_r2_r3_r7()
        self._evaluate_r4()
        
        self.findings.sort(key=lambda f: (f.rule_id, f.title))
        return self.findings

    def _create_evidences(self, locations: list[Any], kind: str, detail: str) -> tuple[Evidence, ...]:
        evs = []
        for loc in locations[:5]:
            # loc could be our scan Location object, or a tuple
            file = getattr(loc, "file", str(loc))
            line = getattr(loc, "line", 0)
            evs.append(Evidence(kind=kind, detail=detail, file=file, line=line))
        return tuple(evs)

    def _evaluate_r1(self) -> None:
        manifest_pkgs = set(self.manifest.runtime.keys()) | set(self.manifest.dev.keys())
        
        for imp_name, locs in self.imports.runtime.items():
            self._check_r1(imp_name, locs, Group.RUNTIME, manifest_pkgs)
            
        for imp_name, locs in self.imports.optional.items():
            self._check_r1(imp_name, locs, Group.OPTIONAL, manifest_pkgs)

    def _check_r1(self, imp_name: str, locs: list[Any], group: Group, manifest_pkgs: set[str], force_low_conf: bool = False) -> None:
        resolution = self.resolver.map_import_to_package(imp_name)
        
        if not resolution.candidates:
            # We don't know what package this belongs to. Low confidence.
            conf = Confidence.LOW
            candidates = [imp_name]
        else:
            cand = resolution.candidates[0]
            conf = Confidence.HIGH if getattr(cand.confidence, "value", cand.confidence) == "High" else Confidence.MEDIUM
            candidates = [c.distribution for c in resolution.candidates]
            
        if force_low_conf:
            conf = Confidence.LOW

        # If any candidate is in the manifest, it's NOT an undeclared import
        if any(c in manifest_pkgs for c in candidates):
            return

        # Undeclared import!
        pkg_display = candidates[0] if len(candidates) == 1 else " | ".join(candidates)
        auto = len(candidates) == 1 and conf != Confidence.LOW
        
        fix = FixAction(
            kind=FixKind.INSTALL_PACKAGE if auto else FixKind.MANUAL,
            target=candidates[0] if auto else pkg_display,
            specifier="",
            tier=1,
            auto=auto
        )
        
        self.findings.append(Finding(
            rule_id="R1",
            severity=self._get_severity("R1"),
            confidence=conf,
            group=group,
            title=self._title("R1"),
            explanation=self._format_msg("R1", package=pkg_display),
            evidence=self._create_evidences(locs, "Import", f"Import '{imp_name}'"),
            fix=fix
        ))

    def _evaluate_r2_r3_r7(self) -> None:
        # Group by canonical name across both runtime and dev for R3 (Conflicting Pins)
        all_declarations: dict[str, list[Location]] = {}
        for pkg, locs in self.manifest.runtime.items():
            all_declarations.setdefault(canonicalize_name(pkg), []).extend(locs)
        for pkg, locs in self.manifest.dev.items():
            all_declarations.setdefault(canonicalize_name(pkg), []).extend(locs)
            
        for canon_pkg, locs in all_declarations.items():
            
            # Actually we can get original name from the first loc if we didn't lose it, but we can just use canon_pkg.
            
            # Check R3: Conflicting Pins
            # A simple static check: try to combine all specifiers. If InvalidRequirement or disjoint, it's conflicting.
            specifier_strs = [loc.specifier for loc in locs if loc.specifier]
            if len(set(specifier_strs)) > 1:
                try:
                    packaging.requirements.Requirement("dummy " + ",".join(specifier_strs))
                    # simple check: if it's "==1.0,==2.0", it's disjoint.
                    # We can't trivially mathematically intersect all bounds here easily without full resolver,
                    # but we can detect == conflicts.
                    equals = [s for s in specifier_strs if s.startswith("==")]
                    if len(set(equals)) > 1:
                        self.findings.append(Finding(
                            rule_id="R3",
                            severity=self._get_severity("R3"),
                            confidence=Confidence.HIGH,
                            group=Group.RUNTIME,
                            title=self._title("R3"),
                            explanation=self._format_msg("R3", package=canon_pkg, conflict_detail="Multiple conflicting '==' pins"),
                            evidence=self._create_evidences(locs, "Declaration", "Conflicting pin"),
                            fix=None
                        ))
                except packaging.requirements.InvalidRequirement:
                    self.findings.append(Finding(
                        rule_id="R3",
                        severity=self._get_severity("R3"),
                        confidence=Confidence.HIGH,
                        group=Group.RUNTIME,
                        title=self._title("R3"),
                        explanation=self._format_msg("R3", package=canon_pkg, conflict_detail="Invalid combined specifier"),
                        evidence=self._create_evidences(locs, "Declaration", "Syntax issue combining pins"),
                        fix=None
                    ))

            # Check R7: Markers and R2: Missing/Version
            for loc in locs:
                if loc.marker:
                    try:
                        marker = packaging.markers.Marker(loc.marker)
                        if not marker.evaluate(self.marker_env):
                            self.findings.append(Finding(
                                rule_id="R7",
                                severity=self._get_severity("R7"),
                                confidence=Confidence.HIGH,
                                group=Group.RUNTIME,
                                title=self._title("R7"),
                                explanation=self._format_msg("R7", package=canon_pkg, marker=loc.marker),
                                evidence=self._create_evidences([loc], "Marker", "Evaluated to False"),
                                fix=None
                            ))
                            continue # Excluded, skip R2
                    except packaging.markers.InvalidMarker:
                        pass # Ignore broken markers
                        
                # R2 Check
                # Create a canonical map of installed packages
                canonical_installed = {canonicalize_name(k): v for k, v in self.fingerprint.installed_packages.items()}
                installed_version = canonical_installed.get(canon_pkg)
                
                if not installed_version:
                    self.findings.append(Finding(
                        rule_id="R2",
                        severity=self._get_severity("R2"),
                        confidence=Confidence.HIGH,
                        group=Group.RUNTIME,
                        title=self._title("R2"),
                        explanation=self._format_msg("R2", package=canon_pkg, specifier=loc.specifier or "any", installed_version="is missing"),
                        evidence=self._create_evidences([loc], "Declaration", "Missing package"),
                        fix=FixAction(FixKind.INSTALL_PACKAGE, canon_pkg, loc.specifier, tier=1, auto=True)
                    ))
                elif loc.specifier:
                    try:
                        spec = packaging.specifiers.SpecifierSet(loc.specifier)
                        if not spec.contains(installed_version):
                            self.findings.append(Finding(
                                rule_id="R2",
                                severity=self._get_severity("R2"),
                                confidence=Confidence.HIGH,
                                group=Group.RUNTIME,
                                title=self._title("R2"),
                                explanation=self._format_msg("R2", package=canon_pkg, specifier=loc.specifier, installed_version=f"has version {installed_version}"),
                                evidence=self._create_evidences([loc], "Declaration", "Version mismatch"),
                                fix=FixAction(FixKind.UPGRADE, canon_pkg, loc.specifier, tier=1, auto=True)
                            ))
                    except packaging.specifiers.InvalidSpecifier:
                        pass

    def _evaluate_r4(self) -> None:
        target_py_ver = self.fingerprint.python_version
        if not target_py_ver:
            return

        if self.manifest.requires_python:
            try:
                spec = packaging.specifiers.SpecifierSet(self.manifest.requires_python)
                if not spec.contains(target_py_ver):
                    self.findings.append(Finding(
                        rule_id="R4",
                        severity=self._get_severity("R4"),
                        confidence=Confidence.HIGH,
                        group=Group.RUNTIME,
                        title=self._title("R4"),
                        explanation=self._format_msg("R4", conflict_detail=f"Project requires {self.manifest.requires_python}, but target environment has {target_py_ver}"),
                        evidence=self._create_evidences([], "Environment", f"Python {target_py_ver}"),
                        fix=FixAction(FixKind.CHANGE_PYTHON, "python", self.manifest.requires_python, tier=1, auto=True)
                    ))
            except packaging.specifiers.InvalidSpecifier:
                pass
