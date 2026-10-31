# Security policy

Camera & Mic Lock is a privacy tool, so security bugs matter more than usual.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem (for example a
way to unblock the camera or microphone without the PIN, or to bypass the
wrong-PIN lockout).

Instead, use GitHub's private reporting:
[Report a vulnerability](https://github.com/saiftherehman/privacy-lock/security/advisories/new)
(the **Security** tab of the repository → **Report a vulnerability**).

Include the platform (Linux distribution / Windows / macOS version), the
steps to reproduce, and what you expected to happen.

## Known limits

Some things are outside what the tool can protect against by design
(administrator / root access, a restart after the lockout, macOS's weaker
model). They are listed in the README under
"What it protects against, and what it doesn't" and are not vulnerabilities.
