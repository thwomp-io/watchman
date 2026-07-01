// Build identity. `false` in the dev source; the OSS publish transform flips it to `true` for the
// PUBLISHED tree (a single-line, file-scoped edit — see scripts/oss_publish.py VERSION_PINS).
//
// What it gates: prod-only product behavior that shouldn't be in the dev daily-driver. First use — the
// pack switcher hides the "Real data" (no-pack → read your real corpus) option in the published app:
// prod centers on weight packs (a bundled demo or your own, loaded explicitly via "Load Weight Pack…"),
// and never surfaces an implicit "read whatever's at ~/projects/corpus" toggle. Dev keeps it.
export const PUBLISHED = true;
