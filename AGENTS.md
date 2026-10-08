# AGENTS.md

## Agent skills

### Issue tracker

Issues live in GitHub Issues for `santiagopedro1/mininet-ai` (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Git conventions

### Commit messages

```
<type>(<scope>): <short summary>

- Bullet point describing change 1
- Bullet point describing change 2
- Bullet point describing change 3
```

- `<type>`: one of `feat`, `fix`, `refactor`, `docs`, `chore`
- `<scope>`: the area of the codebase touched (e.g. `cli`, `substrates`, `docs`)
- Subject line: short, imperative, lowercase, no trailing period
- Body: one bullet point per logical change

### Branch names

When implementing a ticket or feature, create a branch with the following naming convention:

```
<type>/<scope-or-ticket>-<short-description>
```

Examples:

```
feat/user-auth            feat/JIRA-123-login-page
fix/checkout-crash        fix/GH-404-cart-total
refactor/api-client
docs/readme-setup
chore/upgrade-deps
```
