# Security policy

FinPilot handles personal financial data, so please report vulnerabilities privately.

## Report a vulnerability

1. Open this repository's **Security** tab on GitHub and choose **Report a vulnerability**. If that option is not available, contact the repository owner through their GitHub profile and ask for a private channel.
2. Do not open a public issue, pull request or discussion about a suspected vulnerability.
3. Include the affected commit, the steps to reproduce, the impact you observed and any fix you suggest.

The maintainer will acknowledge the report and agree on a fix and a disclosure timeline with you.

## Scope

In scope:

- The application code in `finpilot/`, the deployment files and the scripts in this repository.

Out of scope:

- Third-party services such as Plaid, model providers and external MCP servers.
- Attacks that need physical access to a user's device, and social engineering.

## Current status

FinPilot has no hosted production deployment yet. Weaknesses that are already known are tracked publicly in [ROADMAP.md](ROADMAP.md), under goal `G1. Security and privacy`; please report anything that is not listed there.

## Supported versions

Only the latest commit on `main` receives security fixes.
