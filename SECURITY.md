# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest release | ✅ |
| Older releases | ❌ — please upgrade |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

To report a security issue privately, open a [GitHub Security Advisory](https://github.com/Trumanac/mlrVp/security/advisories/new) (click **Report a vulnerability** on the Security tab).

Include as much detail as possible:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

You can expect an acknowledgement within 7 days. If the issue is confirmed, a fix will be prioritised for the next release.

## Scope

mlrVp is a desktop audio application. Relevant security concerns include:

- **Malicious preset/rack files**: preset and rack JSON files are loaded and deserialized — crafted files should not be able to execute arbitrary code
- **Malicious audio files**: audio files are decoded via `soundfile` / `sounddevice` — crafted files should not cause crashes or code execution
- **OSC / network exposure**: the OSC interface binds to localhost by default — unintended network exposure is a concern
- **VST3/AU plugin loading**: `pedalboard` loads native code plugins — only load plugins from trusted sources

Out of scope: social engineering, physical access, issues in third-party dependencies (report those upstream).
