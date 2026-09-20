from parity.diagnose.engine import Diagnoser
from parity.diagnose.models import Confidence, Finding, Group, Severity
from parity.env.fingerprint import EnvFingerprint
from parity.scan.ast_import import ImportData
from parity.scan.manifest import ManifestData


def test_determinism():
    manifest = ManifestData()
    imports = ImportData()
    fingerprint = EnvFingerprint(
        schema_version="1.0", parity_version="1.0", timestamp="",
        os_name="Linux", os_release="", architecture="", platform_tags=[],
        python_version="3.10.0", python_executable="", is_venv=False, pip_version="",
        installed_packages={"pkg-c": "1.0", "pkg-a": "2.0"}
    )
    
    class DummyResolver:
        def map_import_to_package(self, import_name: str):
            from parity.resolve.mapping import Candidate, ConfidenceEnum, Resolution
            return Resolution(candidates=[Candidate(import_name, "Identity", ConfidenceEnum.LOW)])

    diagnoser = Diagnoser(manifest, imports, fingerprint, DummyResolver())
    
    # inject unordered findings
    diagnoser.findings = [
        Finding("R3", Severity.MEDIUM, Confidence.HIGH, Group.RUNTIME, "Z-Conflicting", "...", (), None),
        Finding("R1", Severity.HIGH, Confidence.HIGH, Group.RUNTIME, "Undeclared A", "...", (), None),
        Finding("R3", Severity.MEDIUM, Confidence.HIGH, Group.RUNTIME, "A-Conflicting", "...", (), None),
        Finding("R1", Severity.HIGH, Confidence.HIGH, Group.RUNTIME, "Undeclared C", "...", (), None),
    ]
    
    # calling evaluate will append new findings and sort them all
    res = diagnoser.evaluate()
    
    assert [f.rule_id for f in res] == ["R1", "R1", "R3", "R3"]
    assert [f.title for f in res] == ["Undeclared A", "Undeclared C", "A-Conflicting", "Z-Conflicting"]
