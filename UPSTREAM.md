# Upstream Source

The vendored mining stack in
`qbitleap-qbitleapbtc/upstream/qbit-mining-bootstrap/` is an unmodified copy of:

- Repository: `https://github.com/Qbit-Org/qbit-mining-bootstrap.git`
- Commit: `2cd9fa4049c2064e34285de77cce4c0731fe657d`
- Upstream branch at import: `main`
- Upstream-reported version: `1.0.0`

Umbrel-specific code must remain outside the vendored directory. The upstream
integrity workflow compares every tracked path, blob, and executable bit against
the pinned commit.

The machine-readable pin is stored in `UPSTREAM_COMMIT`. Updating upstream
replaces the vendored directory with the newer official tree and updates that
pin and this document together.

The scheduled upstream-update workflow checks official `main` weekly. When a
new commit is available, it verifies the replacement snapshot, commits it, and
pushes it directly to this repository's `main` branch. The same commit increments
the Umbrel app patch version, updates its image tags and release notes, and
dispatches builds for all three application images.
