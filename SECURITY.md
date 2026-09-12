# Security policy

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**.

Include what you found, how to reproduce it, and the impact you expect. You'll get an acknowledgement as soon as possible, and credit in the release notes if you'd like.

## Scope

In scope:

- a hook or command that executes unexpected code, or writes outside the repo it was run for
- `mast leakcheck` missing a class of secret it claims to detect
- the learning loop leaking data somewhere other than documented in [docs/privacy-and-security.md](docs/privacy-and-security.md)

Out of scope:

- bypassing `block` rules or shell guards — they are guardrails for a cooperative agent, not a sandbox (see the privacy & security docs)
- commands you configured yourself in `.claude/standards.config.json`, or standards you chose to register
