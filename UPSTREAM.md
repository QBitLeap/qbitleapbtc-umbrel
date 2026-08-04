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

Updating upstream requires reviewing a newer official commit, replacing the
vendored directory with that commit's tree, updating the commit above and in the
integrity workflow, and committing those changes together.

The scheduled upstream-update workflow checks official `main` weekly. When a
new commit is available, it replaces the snapshot and opens or refreshes a pull
request. Updates are never merged automatically.
