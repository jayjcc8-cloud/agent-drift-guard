# Security policy

## Supported versions

Security fixes are applied to the latest released minor version.

## Reporting a vulnerability

Do not open a public issue for credentials exposure, Hook bypasses, unsafe configuration mutation, or data
retention failures. Use GitHub's private vulnerability reporting for this repository. Include the affected
version, reproduction, impact, and any suggested mitigation.

Agent Drift Guard is a guardrail, not a complete sandbox or authorization boundary. Some hosted or specialized
tool paths may not pass through local Hooks. Keep platform permission controls enabled and review generated Hook
configuration before trusting it.
