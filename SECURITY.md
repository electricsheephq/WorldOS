# Security policy

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/electricsheephq/WorldOS/security/advisories/new).
Do not publish exploit details, credentials, private campaign content, or personal
data in a public issue. Include the affected commit, component, expected behavior,
impact, and a minimal sanitized reproduction. No response-time guarantee is made.

## Threat notes

WorldOS includes a stateful engine, renderers, local services, and integrations
with external providers. Treat imported content, assets, and model output as
untrusted; review code and configuration before running them. Report unauthorized
state changes, unintended service exposure, or credential disclosure privately.
Only test systems and accounts you are authorized to access.

## Data handling

Keep provider credentials, private art, campaign saves, and raw personal transcripts
out of public reports. Use synthetic data and placeholders in reproductions, redact
logs and screenshots, and share only the minimum needed to demonstrate the issue.
If a credential may have been exposed, revoke or rotate it through its provider;
do not include the live credential in the report.

This policy does not certify a deployment, a security audit, or third-party code.
