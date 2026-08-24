# Upstream Source

The vendored mining stack in
`qbitleap-qbitleapbtc/upstream/qbit-mining-bootstrap/` is an unmodified copy of:

- Repository: `https://github.com/Qbit-Org/qbit-mining-bootstrap.git`
- Commit: `f28a6bf46afd73de42842e7ca6732b27ac58c25d`
- Upstream branch at import: `main`
- Upstream-reported version: `1.0.0`

Umbrel-specific code must remain outside the vendored directory. The upstream
integrity workflow compares every tracked path, blob, and executable bit against
the pinned commit.

The machine-readable pin is stored in `UPSTREAM_COMMIT`. Updating upstream
replaces the vendored directory with the newer official tree and updates that
pin and this document together.

The scheduled upstream-update workflow checks official `main` weekly. When a
new commit is available, it verifies the replacement snapshot, runs the Umbrel
compatibility tests, and builds all three versioned application images. Only
after every gate succeeds does it increment the Umbrel app patch version and
push the release commit to this repository's `main` branch. A failed gate leaves
the published app version unchanged.
