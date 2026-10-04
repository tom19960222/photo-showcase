## Communication

Always communicate with the user in Traditional Chinese using Taiwanese terminology and phrasing.

## Agent skills

### Issue tracker

Issues live in GitHub Issues via the `gh` CLI. See `docs/agents/issue-tracker.md`.

In the managed Codex environment, sandboxed commands may be unable to resolve `api.github.com`, causing `gh auth status` to incorrectly report an invalid token. When a `gh` command fails to connect, retry the same read-only check with escalated network access before asking the user to re-authenticate. Only treat the token as invalid if the escalated check also fails authentication.

### Triage labels

Uses the default canonical triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: root `CONTEXT.md` and `docs/adr/`. See `docs/agents/domain.md`.
