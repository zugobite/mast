# acme-python (example)

A complete example of a **private standard** layered on the mast core. Copy it as a starting point, or create a fresh one with `mast standard init`.

```
acme-python/
├── standard.json              # name, detect rules, config defaults, privateTerms
├── profiles/
│   ├── acme-base.json         # core + acme-shared
│   ├── acme-api.json          # core + web + fastapi + acme-shared + acme-api
│   └── acme-data.json         # core + data + acme-shared
├── packs/
│   ├── acme-shared/           # company context, wins, grievances, a money rule
│   └── acme-api/              # FastAPI conventions + two rules
└── commands/release-notes.md  # an extra slash command for every Acme repo
```

Try it:

```bash
mast standard check examples/acme-python
# do not `mast standard add` this copy unless you want it on your machine
mast export ~/code/acme-billing --dry-run --standard examples/acme-python
```

Read the guide: [Building a private standard](../../docs/standards.md).
