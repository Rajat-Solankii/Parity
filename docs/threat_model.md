# Parity Threat Model

This document outlines the security assumptions, mitigations, and known residual risks for Parity's environment fingerprinting and resolution phases.

## Mitigations

1. **Target Environment Execution Isolation:** Parity never blindly executes scripts within an untrusted target environment (e.g., a venv found in a random project folder). Parity uses static `pyvenv.cfg` parsing as the primary method to locate `site-packages`. When falling back to `sysconfig`, it forces the interpreter to skip initialization (`-S -I`), neutralizing standard `.pth` exploits. 
2. **Safe Tool Probing:** Parity prevents arbitrary code execution when discovering native tools. Tools listed in `tool_catalog.json` are probed for their version using a short subprocess timeout and `shell=False`. The probing is executed with a neutral working directory (`cwd=tempfile.TemporaryDirectory()`) and a sanitized `PATH` to prevent directory traversal or picking up trojan executables planted in the project directory. User-extended tools (`--tool`) are never executed.
3. **Data-Only `.pth` Parsing:** When attempting to discover editable installs via `.pth` files, Parity parses them as text and explicitly ignores lines containing `import `.
4. **Online Verification:** When resolving packages online (Layer 4), Parity strictly communicates with `files.pythonhosted.org`, verifies the wheel's SHA-256 hash immediately upon streaming *before* opening the zip archive, and bounds its reads when extracting metadata to prevent zip-bomb/DoS attacks.

## Known Residual Risks

1. **Symlinks out of environment:** While Parity does not follow symlinks out of the environment for metadata directly, deeply nested symbolic loops within site-packages could theoretically impact traversal speed.
2. **Oversized / Malformed METADATA:** `importlib.metadata` handles parsing of `METADATA` files locally. A maliciously crafted, extremely oversized `METADATA` file within the local `site-packages` could cause elevated memory consumption.
3. **Zero-Day Interpreter Vulnerabilities:** The `-S -I` flags neutralize `.pth` files during Python startup. However, fundamental vulnerabilities in the Python interpreter executable itself (triggered simply by invoking the binary) remain out of scope for Parity's mitigation layer.
