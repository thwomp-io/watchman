// The VAULT zone: the tracker/ corpus, browsable read-only inside the console.
// Left rail = a recursive folder tree (mirrors the Obsidian vault, folder-notes collapsed). Main =
// the selected doc rendered (frontmatter drawer + GFM markdown). Complements Obsidian (editing +
// graph stay there) — this is the read/operate surface.

import { memo, useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { listVaultDocs, onVaultChanged, readDoc, readImage } from "./api";
import ErrorBoundary from "./ErrorBoundary";
import { useNav } from "./nav";
import type { VaultDoc } from "./types";

function splitFrontmatter(raw: string): { fm: string; body: string } {
  const m = raw.match(/^---\n([\s\S]*?)\n---\n?/);
  return m ? { fm: m[1], body: raw.slice(m[0].length) } : { fm: "", body: raw };
}

// [[target|label]] / [[target]] -> links the `a` renderer routes in-console; ![[embed]] -> an image
// the `img` renderer loads via the `vaultimg:` scheme (embeds carry vault-absolute paths).
// Exported so the doc_series panel (Dash) reuses the SAME wikilink transform — one renderer, two
// consumers (VaultZone resolves in-zone; doc_series cross-navigates to VAULT).
export function preprocessLinks(body: string): string {
  return body
    .replace(/!\[\[([^\]]+)\]\]/g, (_m, t) => {
      const p = String(t).split("|")[0].trim();
      return `![${p.split("/").pop()}](vaultimg:${encodeURIComponent(p)})`;
    })
    .replace(/\[\[([^\]|]+)(?:\\?\|([^\]]+))?\]\]/g, (_m, target, label) => {
      // strip a trailing backslash: a wikilink in a MARKDOWN TABLE cell escapes its alias pipe as
      // `\|`, and we run before the table parser unescapes it, so the `\` lands on the target tail.
      const t = String(target).replace(/\\+$/, "").trim();
      const l = (label ? String(label) : t.split("/").pop() || t).trim();
      return `[${l}](wiki:${encodeURIComponent(t)})`;
    });
}

// Resolve a markdown image src to a vault-relative path. `vaultimg:` carries a vault-absolute path
// (from ![[embed]]); a plain src is relative to the doc's own dir (handles ./ and ../).
function joinVault(docDir: string, src: string): string {
  if (src.startsWith("vaultimg:")) return decodeURIComponent(src.slice("vaultimg:".length));
  const out = docDir ? docDir.split("/") : [];
  for (const seg of src.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") out.pop();
    else out.push(seg);
  }
  return out.join("/");
}

// Loaded images are cached by vault-path for the session — a data URI is deterministic per file, so
// re-mounts (tree toggles, parent re-renders) serve synchronously instead of re-fetching + flashing.
const imgCache = new Map<string, string>();

// Async-loads a vault image as a data URI (the text read path can't carry binary). Shows a quiet
// chip while loading / if unavailable, so a missing asset never breaks the doc.
export function VaultImage({ src, alt, docDir }: { src?: string; alt?: string; docDir: string }) {
  const path = src ? joinVault(docDir, src) : "";
  const [data, setData] = useState<string | null>(() => imgCache.get(path) ?? null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!path) return;
    const hit = imgCache.get(path);
    if (hit) {
      setData(hit);
      setFailed(false);
      return;
    }
    let live = true;
    setData(null);
    setFailed(false);
    readImage(path)
      .then((d) => {
        imgCache.set(path, d);
        if (live) setData(d);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [path]);
  const label = alt || src?.split("/").pop() || "image";
  if (failed) return <code className="vault-asset">🖼 {label} — unavailable</code>;
  if (!data) return <code className="vault-asset">🖼 {label} — loading…</code>;
  return <img className="vault-img" src={data} alt={alt || ""} loading="lazy" />;
}

// Build a minimal VaultDoc straight from a vault path — so a deep-link `target` can open even when the
// doc isn't found in the in-memory list (a listing race, or a path the tree didn't surface). `select`
// only needs `.path` to `readDoc` (containment-checked, list-independent); the rest feeds the tree.
function docFromPath(path: string): VaultDoc {
  const segs = path.split("/").filter(Boolean);
  const file = segs[segs.length - 1] ?? path;
  const stem = file.replace(/\.md$/i, "");
  return {
    path,
    area: segs[0] ?? "",
    dir: segs.slice(0, -1).join("/"),
    name: stem,
    title: stem,
    kind: /\.(jpe?g|png|gif|webp|avif|svg)$/i.test(file) ? "image" : "doc",
  };
}

function resolveWiki(target: string, docs: VaultDoc[]): VaultDoc | null {
  const clean = decodeURIComponent(target).split("#")[0].replace(/\\/g, "/").trim();
  const slug = clean.split("/").filter(Boolean).pop() || clean;
  return (
    docs.find((d) => d.name === slug) ||
    docs.find((d) => d.path.endsWith(`/${slug}.md`) || d.path === `${slug}.md`) ||
    docs.find((d) => d.path.endsWith(`/${slug}/${slug}.md`)) ||
    null
  );
}

// ————— the folder tree —————————————————————————————————————————————————————————————————————————
interface TreeNode {
  name: string;
  prefix: string;
  doc?: VaultDoc; // set for leaf files AND folder-notes (a folder that IS a doc)
  children: Map<string, TreeNode>;
}

// folder-note collapse: a/b/b.md → the folder `b` carries the note (Obsidian's model)
function nodePrefix(d: VaultDoc): string[] {
  const segs = d.path.replace(/\.md$/, "").split("/");
  if (segs.length >= 2 && segs[segs.length - 1] === segs[segs.length - 2]) segs.pop();
  return segs;
}

function buildTree(docs: VaultDoc[]): TreeNode {
  const root: TreeNode = { name: "", prefix: "", children: new Map() };
  for (const d of docs) {
    const segs = nodePrefix(d);
    let node = root;
    segs.forEach((seg, i) => {
      const prefix = segs.slice(0, i + 1).join("/");
      let child = node.children.get(seg);
      if (!child) {
        child = { name: seg, prefix, children: new Map() };
        node.children.set(seg, child);
      }
      node = child;
    });
    node.doc = d;
  }
  return root;
}

function docCount(node: TreeNode): number {
  let n = node.doc ? 1 : 0;
  for (const c of node.children.values()) n += docCount(c);
  return n;
}

function sortedChildren(node: TreeNode): TreeNode[] {
  return [...node.children.values()].sort((a, b) => {
    const af = a.children.size > 0;
    const bf = b.children.size > 0;
    if (af !== bf) return af ? -1 : 1; // folders before files
    return a.name.localeCompare(b.name);
  });
}

function TreeRow({
  node,
  depth,
  openSet,
  toggle,
  selectedPath,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  openSet: Set<string>;
  toggle: (p: string) => void;
  selectedPath: string | null;
  onSelect: (d: VaultDoc) => void;
}) {
  const kids = sortedChildren(node);
  const hasKids = kids.length > 0;
  const isOpen = openSet.has(node.prefix);
  const active = node.doc != null && node.doc.path === selectedPath;
  const label = node.doc ? node.doc.title : node.name.replace(/-/g, " ");
  return (
    <div className="vault-node">
      <div className={`vault-row ${active ? "active" : ""}`} style={{ paddingLeft: 6 + depth * 14 }}>
        {hasKids ? (
          <button className="vault-chev" onClick={() => toggle(node.prefix)}>
            {isOpen ? "▾" : "▸"}
          </button>
        ) : (
          <span className="vault-chev-spacer" />
        )}
        <button
          className={`vault-label ${
            node.doc ? (node.doc.kind === "image" ? "is-image" : "is-doc") : "is-folder"
          }`}
          title={node.doc ? node.doc.path : node.prefix}
          onClick={() => (node.doc ? onSelect(node.doc) : toggle(node.prefix))}
        >
          {label}
        </button>
        {hasKids && <span className="vault-count">{docCount(node)}</span>}
      </div>
      {hasKids && isOpen && (
        <div className="vault-children">
          {kids.map((k) => (
            <TreeRow
              key={k.prefix}
              node={k}
              depth={depth + 1}
              openSet={openSet}
              toggle={toggle}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// memo: VaultZone's only prop is the nav `target`, so with the stabilized nav context this stays
// decoupled from App's once-a-second clock re-render — without it the whole markdown tree (+ images)
// re-rendered every second (the flash). `target` (a vault doc path) deep-links a specific doc.
function VaultZone({ target }: { target?: string }) {
  const nav = useNav();
  const [docs, setDocs] = useState<VaultDoc[]>([]);
  const [selected, setSelected] = useState<VaultDoc | null>(null);
  const [raw, setRaw] = useState<string>("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set(["travel"]));
  const [query, setQuery] = useState("");

  useEffect(() => {
    void listVaultDocs().then((ds) => {
      setDocs(ds);
      if (target) return; // a deep-link target opens via the effect below — don't flash the default first
      const first = ds.find((d) => d.area === "travel") ?? ds[0];
      if (first) select(first);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // target-driven open (deep-link / back-forward); guarded against the report→target round-trip loop.
  // Wait for the list (so a real match expands the tree), then open — falling back to a direct read of
  // the path when the doc isn't in the list, so a cross-zone deep-link never dead-ends on the default.
  useEffect(() => {
    if (!target || docs.length === 0 || selected?.path === target) return;
    const d = docs.find((x) => x.path === target);
    select(d ?? docFromPath(target));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, docs]);

  // keep the nav `current` in sync with the open doc (so a later navigate / tab-switch captures it)
  useEffect(() => {
    if (selected) nav.report({ zone: "vault", doc: selected.path });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const openAncestors = (d: VaultDoc) => {
    const segs = nodePrefix(d);
    setOpen((o) => {
      const n = new Set(o);
      segs.forEach((_, i) => n.add(segs.slice(0, i + 1).join("/")));
      return n;
    });
  };

  const select = (d: VaultDoc) => {
    setSelected(d);
    setRaw("");
    setError("");
    openAncestors(d);
    if (d.kind === "image") return; // the stage renders the image directly (binary, not via readDoc)
    readDoc(d.path)
      .then(setRaw)
      .catch((e) => setError(String(e)));
  };

  const toggle = (prefix: string) =>
    setOpen((o) => {
      const n = new Set(o);
      if (n.has(prefix)) n.delete(prefix);
      else n.add(prefix);
      return n;
    });

  // Re-fetch the tree + re-read the open doc (catches in-place edits). Stable identity (empty deps;
  // reads current `selected` via the setState updater) so the fs-watch subscription never goes stale.
  const reload = useCallback(() => {
    void listVaultDocs().then((ds) => {
      setDocs(ds);
      setSelected((cur) => {
        if (cur && cur.kind === "doc") readDoc(cur.path).then(setRaw).catch(() => {});
        return cur;
      });
    });
  }, []);

  // near-real-time: the Rust fs-watcher emits `vault-changed` on any tracker/ doc/image add/remove/edit
  useEffect(() => {
    const un = onVaultChanged(reload);
    return () => {
      void un.then((f) => f());
    };
  }, [reload]);

  const root = useMemo(() => buildTree(docs), [docs]);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? docs.filter((d) => `${d.path} ${d.title}`.toLowerCase().includes(q)) : [];
  }, [docs, query]);
  const searching = query.trim().length > 0;

  const { fm, body } = useMemo(() => splitFrontmatter(raw), [raw]);
  const processed = useMemo(() => preprocessLinks(body), [body]);

  return (
    <div className="vault-zone">
      <nav className="vault-rail bezel">
        <div className="vault-rail-head">
          <input
            className="vault-search"
            placeholder="FILTER DOCS…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="vault-refresh" title="Refresh (auto-updates on file changes)" onClick={reload}>
            ⟳
          </button>
        </div>
        {searching ? (
          <div className="vault-search-results">
            <span className="vault-dir-label">{matches.length} MATCH{matches.length === 1 ? "" : "ES"}</span>
            {matches.map((d) => (
              <div key={d.path} className="vault-row" style={{ paddingLeft: 8 }}>
                <span className="vault-chev-spacer" />
                <button
                  className={`vault-label is-doc ${selected?.path === d.path ? "active-text" : ""}`}
                  title={d.path}
                  onClick={() => select(d)}
                >
                  {d.title}
                  <em className="vault-match-path">{d.dir}</em>
                </button>
              </div>
            ))}
          </div>
        ) : (
          sortedChildren(root).map((n) => (
            <TreeRow
              key={n.prefix}
              node={n}
              depth={0}
              openSet={open}
              toggle={toggle}
              selectedPath={selected?.path ?? null}
              onSelect={select}
            />
          ))
        )}
        {docs.length === 0 && <p className="empty">NO DOCS IN THE VAULT</p>}
      </nav>

      <section className="vault-stage bezel">
        {selected && (
          <header className="vault-stage-head">
            <span className="lane-tag">{selected.area}</span>
            <span className="surface-when">{selected.path}</span>
          </header>
        )}
        {error && <p className="surface-error">{error}</p>}
        <ErrorBoundary resetKey={selected?.path ?? ""}>
          <div className="vault-doc-view">
            {selected?.kind === "image" && (
              <div className="vault-image-view">
                <VaultImage
                  src={`vaultimg:${encodeURIComponent(selected.path)}`}
                  alt={selected.name}
                  docDir=""
                />
              </div>
            )}
            {selected?.kind !== "image" && fm && (
              <details className="vault-frontmatter" open>
                <summary>FRONTMATTER</summary>
                <pre>{fm}</pre>
              </details>
            )}
            {selected?.kind !== "image" && raw && (
              <article className="vault-md">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  // the vault is the maintainer's own trusted local content — identity transform so our custom
                  // `wiki:` + `vaultimg:` schemes survive (react-markdown strips unknown protocols)
                  urlTransform={(url) => url}
                  components={{
                    a({ href, children }) {
                      if (href?.startsWith("wiki:")) {
                        const hit = resolveWiki(href.slice(5), docs);
                        return (
                          <a
                            className={`wikilink ${hit ? "" : "dead"}`}
                            href="#"
                            title={hit ? hit.path : "unresolved link"}
                            onClick={(e) => {
                              e.preventDefault();
                              if (hit) select(hit);
                            }}
                          >
                            {children}
                          </a>
                        );
                      }
                      return (
                        <a href={href} target="_blank" rel="noreferrer">
                          {children}
                        </a>
                      );
                    },
                    img({ src, alt }) {
                      return (
                        <VaultImage
                          src={typeof src === "string" ? src : undefined}
                          alt={alt}
                          docDir={selected?.dir ?? ""}
                        />
                      );
                    },
                  }}
                >
                  {processed}
                </ReactMarkdown>
              </article>
            )}
            {!selected && <p className="empty">SELECT A DOC FROM THE RAIL</p>}
          </div>
        </ErrorBoundary>
      </section>
    </div>
  );
}

export default memo(VaultZone);
