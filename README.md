# registry-mirror

<!-- szl:header v1 -->
<!-- Native identity: szl-brand@4309f6fce89179331b0b3ed8c99494e76c5be256; Orbit v2 icon. -->
<p>
  <a href="https://github.com/szl-holdings">
    <img src="https://raw.githubusercontent.com/szl-holdings/szl-brand/4309f6fce89179331b0b3ed8c99494e76c5be256/kit/logos/orbit-v2/suite/szl-icon-32.svg" alt="SZL Holdings — Orbit v2" width="32" height="32" />
  </a>
</p>

**Control before action. Evidence after.**

[Product](https://a-11-oy.com) · [Proof](https://a11oy.net) · [GitHub organization](https://github.com/szl-holdings)
<!-- /szl:header -->

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

`SETUP_REQUIRED` until the two repository secrets exist. Without them the job logs a warning, uploads
a `SETUP_REQUIRED` receipt, and exits 0; nothing is mirrored and nothing is claimed. Tool-install
and registry-authentication failures also produce sanitized failure receipts before artifact upload.
Codename images (`rosie`, `sentra`, `amaru`) and
`bundles-staging/*` are excluded by doctrine.

With credentials installed, the Authenticate step proves the Docker Hub pair before any
mirroring with exactly one token request (`pull,push` on the first destination repository; no
retries, no redirects; credentials sent on stdin, never in argv or logs). `crane auth login`
alone makes no network call, so it cannot catch a wrong pair. The outcome is recorded in the
receipt as `REGISTRY_AUTH_FAILED` (401), `REGISTRY_AUTH_LOCKED_OUT` (429) or
`REGISTRY_AUTH_UNVERIFIED` (any other answer without a token, including no response), with
the HTTP status. Each fails the run before the mirror loop, so a wrong pair costs one login
per run instead of one per image. Stored-secret defects that cannot be seen after
`gh secret set` (leading/trailing whitespace or newline, embedded whitespace, surrounding
quotes, non-ASCII or control characters, an email or access token used as the username,
or other characters a Docker ID cannot contain) are logged as `DOCKERHUB_SECRET_DEFECT`
warnings and recorded as `secret_defects` categories in a failure receipt. Values are
never printed.

During mirroring, a registry authorization failure (401/403) or rate limit (429)
stops the remaining image/tag attempts, retains a failure receipt, and fails the run. This
prevents repeated bad-login attempts from escalating into a registry lockout. The owner
must resolve the registry access failure before retrying; the workflow does not rotate or
alter credentials. Other copy failures and digest mismatches also remain failures.
Before copying a tag, a failed destination digest lookup permits first-copy only
when its final error explicitly reports a missing manifest/repository or HTTP 404.
Only complete recognized crane absence diagnostics qualify; compound or novel
error formats remain failures even when they contain absence tokens.
Other lookup failures retain a `DESTINATION_DIGEST_FAILED` receipt and skip that
tag without writing to its unknown destination state.

The mirror loop contract is tested offline with `python3 -m unittest discover -s tests -v`
(Python 3, Bash, and `jq` required); no registry credentials or network access are used.

## Owner step (once, and again to rotate)

1. Sign in to Docker Hub as an account that can push to `szlholdings`. Go to Account settings → Personal access tokens → New token, scope **Read & Write**, description `szl registry-mirror`. When rotating, also revoke the previous `szl registry-mirror` token.
2. Optional: check the pair once on your own machine with `docker login -u <docker-id>` and paste the token at the password prompt.
3. In an elevated PowerShell with `gh auth status` green. `<docker-id>` is the Docker ID (the lowercase name in `hub.docker.com/u/<docker-id>`), not the account email. Paste the token at the interactive prompt with no quotes, spaces or trailing newline:

```
gh secret set DOCKERHUB_USERNAME --repo szl-holdings/registry-mirror --body "<docker-id>"
gh secret set DOCKERHUB_TOKEN    --repo szl-holdings/registry-mirror
gh workflow run mirror-dockerhub.yml --repo szl-holdings/registry-mirror
```

   If the last receipt says `REGISTRY_AUTH_LOCKED_OUT`, wait for Docker Hub's login lockout to clear before running the workflow.
4. Read back: `gh run list --repo szl-holdings/registry-mirror --workflow mirror-dockerhub.yml` and the receipt artifact. The Authenticate log should show `HTTP 200 (REGISTRY_AUTH_VERIFIED)`. Docker Hub repositories appear under https://hub.docker.com/u/szlholdings only after a verified copy.

## Why a separate repository

The org `.github` repository enforces a static control-plane effect boundary that denies undeclared
workflows by design (PR #820 there was denied with `WORKFLOW_DECLARATION_MISSING` and closed). A mirror
job with an external write and a registry credential belongs behind its own repository boundary with
repository-scoped secrets, not inside the org trust root.

Apache-2.0. Λ = Conjecture 1.
