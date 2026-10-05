# registry-mirror

One-purpose repository: mirror the canonical, provenance-attested images in `ghcr.io/szl-holdings/*`
to `docker.io/szlholdings/*` by digest-preserving copy. GHCR stays the source of truth; Docker Hub is
a discovery surface with byte-identical tags.

| | |
|---|---|
| Workflow | `.github/workflows/mirror-dockerhub.yml` (daily 05:23 UTC, and on dispatch) |
| Images | a11oy, a11oy-bundle, hatun-mcp, killinchu, killinchu-bundle, szl-mesh, szl-receipts, szl-receipts-server, szl-uds-bundle |
| Tags | `latest` and release-looking tags (`x.y.z`, `vx.y.z`, `uds-vx.y.z`); never per-commit `sha-*` tags |
| Tooling | `crane` v0.22.1, release tarball pinned by sha256; every `uses:` pinned to a 40-char SHA |
| Verification | source and destination digests compared after every copy; mismatch fails the run |
| Receipt | `mirror-receipt.json` + `receipt.jsonl` uploaded per run (90 days) |

## State

`SETUP_REQUIRED` until the two repository secrets exist. Without them the job logs a warning and exits 0;
nothing is mirrored and nothing is claimed. Codename images (`rosie`, `sentra`, `amaru`) and
`bundles-staging/*` are excluded by doctrine.

With credentials installed, a registry authorization failure (401/403) or rate limit (429)
stops the remaining image/tag attempts, retains a failure receipt, and fails the run. This
prevents repeated bad-login attempts from escalating into a registry lockout. The owner
must resolve the registry access failure before retrying; the workflow does not rotate or
alter credentials. Other copy failures and digest mismatches also remain failures.

The mirror loop contract is tested offline with `python3 -m unittest discover -s tests -v`
(Python 3, Bash, and `jq` required); no registry credentials or network access are used.

## Owner step (once)

1. Docker Hub → Account settings → Personal access tokens → New token, scope **Read & Write**, description `szl registry-mirror`.
2. In an elevated PowerShell with `gh auth status` green:

```
gh secret set DOCKERHUB_USERNAME --repo szl-holdings/registry-mirror --body "<docker hub username>"
gh secret set DOCKERHUB_TOKEN    --repo szl-holdings/registry-mirror
gh workflow run mirror-dockerhub.yml --repo szl-holdings/registry-mirror
```

3. Read back: `gh run list --repo szl-holdings/registry-mirror --workflow mirror-dockerhub.yml` and the receipt artifact. Docker Hub repositories appear under https://hub.docker.com/u/szlholdings only after a verified copy.

## Why a separate repository

The org `.github` repository enforces a static control-plane effect boundary that denies undeclared
workflows by design (PR #820 there was denied with `WORKFLOW_DECLARATION_MISSING` and closed). A mirror
job with an external write and a registry credential belongs behind its own repository boundary with
repository-scoped secrets, not inside the org trust root.

Apache-2.0. Λ = Conjecture 1.
