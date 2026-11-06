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

[![Mirror GHCR images to Docker Hub](https://github.com/szl-holdings/registry-mirror/actions/workflows/mirror-dockerhub.yml/badge.svg)](https://github.com/szl-holdings/registry-mirror/actions/workflows/mirror-dockerhub.yml)
[![Mirror loop contract](https://github.com/szl-holdings/registry-mirror/actions/workflows/mirror-contract.yml/badge.svg)](https://github.com/szl-holdings/registry-mirror/actions/workflows/mirror-contract.yml)

One-purpose repository: mirror the canonical, provenance-attested images in `ghcr.io/szl-holdings/*`
to `docker.io/szlholdings/*` by digest-preserving copy. GHCR stays the source of truth; Docker Hub is
a discovery surface with byte-identical tags.

| | |
|---|---|
| Workflow | `.github/workflows/mirror-dockerhub.yml` (daily 05:23 UTC, and on dispatch) |
| Images | a11oy, a11oy-bundle, hatun-mcp, killinchu, killinchu-bundle, szl-mesh, szl-receipts, szl-receipts-server, szl-uds-bundle |
| Tags | `latest` and release-looking tags (`x.y.z`, `vx.y.z`, `uds-vx.y.z`; `x.y.z` and `vx.y.z` may carry a `-suffix` such as `0.4.0-upstream`); never per-commit `sha-*` tags |
| Tooling | `crane` v0.22.1, release tarball pinned by sha256; every `uses:` pinned to a 40-char SHA |
| Verification | source and destination digests compared after every copy; mismatch fails the run |
| Receipt | `mirror-receipt.json` + `receipt.jsonl` uploaded per run (90 days) |

## Current status

Measured 2026-11-06: **nothing has been mirrored, because Docker Hub rejects the stored
credentials.** Both repository secrets are installed, and all 37 scheduled runs since 2026-10-01
have failed on Docker Hub authentication (HTTP 401 from `auth.docker.io`). Every run since the
credential preflight landed ([#13](https://github.com/szl-holdings/registry-mirror/pull/13),
2026-11-03) records `REGISTRY_AUTH_FAILED` (HTTP 401)
and the secret defect `TOKEN_NON_ASCII_OR_CONTROL_CHARACTERS`
([latest run](https://github.com/szl-holdings/registry-mirror/actions/runs/42168499908)).
The `szlholdings` Docker Hub organization has 0 repositories. The source side is healthy: all
nine GHCR images list their release tags anonymously. The fix is the owner step below, with a new
token; the first badge above shows the live result.

## Run outcomes

Without the two repository secrets the job logs a warning, uploads a `SETUP_REQUIRED` receipt,
and exits 0; nothing is mirrored and nothing is claimed. Tool-install
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

## Verify

The tests extract the workflow's inline Bash (credential preflight, mirror loop, receipt
finalizer) from the YAML and run it against fake `crane` and `curl` stand-ins, so the code under
test is the code that ships:

```
python3 -m unittest discover -s tests -v
```

Python 3 (standard library only; run on 3.11 to 3.14), Bash and `jq` are required; no registry
credentials or network access are used. CI runs the same command on pull requests to and pushes
on `main` (`Mirror loop contract`).

## Proof boundary

- A `MIRRORED_VERIFIED` or `ALREADY_IDENTICAL` receipt entry shows that the Docker Hub tag
  resolved to the same manifest digest as the GHCR digest inspected in that run. The workflow does
  not re-check the image's provenance, and a GHCR tag that moves later is only caught up on the
  next run.
- Only the matched tags are copied (`crane copy <source>@<digest> <destination>`). Signatures and
  attestations that GHCR stores under their own tags, such as cosign `sha256-<digest>.sig` and
  `.att` tags or `sha256-<digest>` referrer fallbacks (all three kinds exist on
  `ghcr.io/szl-holdings/a11oy`, checked 2026-11-06), are not mirrored. Check provenance against
  GHCR by digest.
- The offline tests prove that the inline Bash behaves as specified against stand-ins. They do not
  prove that Docker Hub accepts the stored credentials or that any image was mirrored; only a green
  `Mirror GHCR images to Docker Hub` run and its receipt show that.

## Owner step (once, and again to rotate)

1. Sign in to Docker Hub as an account that can push to `szlholdings` (an organization, so this is a member account). Go to Account settings → Personal access tokens → New token, scope **Read & Write**, description `szl registry-mirror`. When rotating, also revoke the previous `szl registry-mirror` token.
2. Optional: check the pair once on your own machine with `docker login -u <docker-id>` and paste the token at the password prompt.
3. In an elevated PowerShell with `gh auth status` green. `<docker-id>` is the Docker ID of that member account (the lowercase name in `hub.docker.com/u/<docker-id>`), not the account email and not the organization name `szlholdings`. Paste the token at the interactive prompt with no quotes, spaces or trailing newline:

```
gh secret set DOCKERHUB_USERNAME --repo szl-holdings/registry-mirror --body "<docker-id>"
gh secret set DOCKERHUB_TOKEN    --repo szl-holdings/registry-mirror
gh workflow run mirror-dockerhub.yml --repo szl-holdings/registry-mirror
```

   If the last receipt says `REGISTRY_AUTH_LOCKED_OUT`, wait for Docker Hub's login lockout to clear before running the workflow.
   A `DOCKERHUB_SECRET_DEFECT` warning of `TOKEN_NON_ASCII_OR_CONTROL_CHARACTERS` means the stored token contains a byte that no Docker Hub token contains, for example a byte-order mark, a non-breaking or zero-width space, or a control character added while pasting. Revoke that token, create a new one and set it again; `gh secret set` can also read the value from standard input (see `gh secret set --help`). The next run reports any remaining defect by category.
4. Read back: `gh run list --repo szl-holdings/registry-mirror --workflow mirror-dockerhub.yml` and the receipt artifact. The Authenticate log should show `HTTP 200 (REGISTRY_AUTH_VERIFIED)`. Docker Hub repositories appear under https://hub.docker.com/u/szlholdings only after a verified copy.

## Why a separate repository

The org `.github` repository enforces a static control-plane effect boundary that denies undeclared
workflows by design (PR #820 there was denied with `WORKFLOW_DECLARATION_MISSING` and closed). A mirror
job with an external write and a registry credential belongs behind its own repository boundary with
repository-scoped secrets, not inside the org trust root.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Λ = Conjecture 1.

Estate on Hugging Face: models, datasets and Spaces are published under the [SZLHOLDINGS](https://huggingface.co/SZLHOLDINGS) org.
