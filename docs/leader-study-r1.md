# registry-mirror domain leader study r1 (propose-only)

Date: 2026-11-26. Method: web-verified leader landscape + owner-direct execution under standing authority GREENLIGHT-RULING-2026-11-24-K1 rank5 OUROBOROS_NEXT_TRANCHE. The Kimi governing seat remains stood down per explicit owner directive (disclosed); owner-direct nonce T24W-OWNER-DIRECT-W-20261126-K1 (W = webseat lane discriminator, T19 collision lesson applied). Study pinned to HEAD c18f37e229dc0ac21a2032124a007fbd2cfb182d, fetched 2026-11-26. This card is documentation only; it changes no code, CI, config, or existing PR status. All absorb actions are FUTURE PROPOSALS (non-authorizing); implementation and merges remain separately reviewed and gated. L Conjecture 1: OPEN (advisory, never a closed theorem). No speedup, energy, or proven-trust claims; upstream performance claims are labeled as upstream claims only. No unverified signing, attestation, or provenance-level claims; this study verifies nothing about this repo's artifacts.

## Domain: one-purpose digest-preserving GHCR-to-Docker-Hub mirror (crane pinned by sha256, workflow uses pinned to 40-char SHAs, per-run receipts, credential preflight, fail-closed on auth failures; current status: zero images mirrored, Docker Hub 401, owner-gated credential fix).

## Leaders studied (web-verified 2026-11-26)
1. go-containerregistry — https://github.com/google/go-containerregistry — the home of crane, the exact copy tool this repo pins (v0.22.1 release tarball by sha256); moat is digest-addressed copy semantics: copy by digest, verify after copy.
2. distribution — https://github.com/distribution/distribution — the reference OCI registry implementation; moat is the canonical registry error/status semantics (401/404/429) the mirror's fail-closed classification is built against.
3. actions/checkout — https://github.com/actions/checkout — the archetype SHA-pinned GitHub Action; moat is the pin-to-40-char-commit discipline this repo applies to every uses:.

## Absorb actions (FUTURE PROPOSALS, non-authorizing)
1. (go-containerregistry) Propose a documentation-only review of crane copy/digest-verify conventions against the mirror loop's source-and-destination digest comparison. Cost: doc-only; limit: note only.
2. (distribution) Propose a documentation-only note mapping REGISTRY_AUTH_FAILED (401) / LOCKED_OUT (429) / UNVERIFIED classifications to reference-registry semantics so the classes stay honest. Cost: doc-only; limit: note only.
3. (actions/checkout) Propose a documentation-only review of workflow pinning conventions (every uses: named with its 40-char SHA rationale) as an example card for the estate. Cost: doc-only; limit: note only.
4. Baseline preserve honestly: the current measured status stays verbatim (0 repositories on Docker Hub; 37 failed runs; REGISTRY_AUTH_FAILED with TOKEN_NON_ASCII_OR_CONTROL_CHARACTERS secret defect; owner credential step pending); no mirror or credential action is proposed by this card; values are never printed.


## Do not do (per governing seat)

Do not merge, bypass the signature wall, or claim implementation beyond this card.
