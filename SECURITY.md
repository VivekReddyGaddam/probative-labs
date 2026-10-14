# Security policy

Probative Labs processes sensitive operational data, so we take security reports seriously.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting ("Report a vulnerability" under the Security tab of this repository). We aim to acknowledge reports within 3 business days.

## Scope

- Leaks of identifiers that should have been pseudonymized
- Ways to recover a person's identity from evidence packs or results
- Code execution through crafted input files or configuration
- Dependency vulnerabilities that affect Probative Labs

## Data handling notes for users

- Keep the pseudonymization key (`.probative/pseudonym.key` or `PROBATIVE_PSEUDONYM_KEY`) secret. Anyone with the key can test whether a known ID appears in the data.
- Do not commit customer data or keys. The default `.gitignore` excludes `data/`, `.probative/` and `*.key`.
