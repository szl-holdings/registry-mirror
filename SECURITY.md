# Security policy

Report vulnerabilities privately through GitHub Security Advisories on this repository,
or per the organisation policy at https://github.com/szl-holdings/.github/blob/main/SECURITY.md.
Please do not open public issues for undisclosed vulnerabilities.

Supply chain: the mirror workflow installs `crane` only from the pinned `v0.22.1` release
tarball and stops unless its sha256 matches the value pinned in
`.github/workflows/mirror-dockerhub.yml`. Every `uses:` reference in this repository is pinned
to a full 40-character commit SHA.

Credentials: `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` are repository secrets that only the
mirror job references. The token reaches `curl` and `crane` only on standard input, never in
argv. The username also reaches `curl` on standard input (inside the Basic `Authorization`
header), but `crane auth login` receives it as its `-u` argument. Neither value is printed;
stored-secret defects are reported by category only. The Scorecard caller passes no secrets to
the organisation's reusable workflow. If the Docker Hub token may be exposed, revoke it in
Docker Hub and rotate it as described in the README's Owner step.
