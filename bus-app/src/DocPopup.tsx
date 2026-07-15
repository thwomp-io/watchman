// DocPopup — the in-console quick-look primitive: any vault doc rendered in a modal overlay
// without leaving the current zone. Born for the bead family tree (click a tile → the full
// Jira-shaped ticket, no zone switch), generic to any surface that wants a doc peek.
//
// Mechanics: a React PORTAL to document.body (a fixed overlay inside the Studio grid would
// anchor to react-grid-layout's transformed items — the portal is immune), the doc_series
// markdown recipe (preprocessLinks + identity urlTransform + the VaultImage embed handler),
// frontmatter stripped like the VAULT zone does. Wikilinks swap the doc IN the popup (a local
// stack backs the ◀), and the header's "open in VAULT ↗" rides the nav primitive — so the
// masthead back button returns here, same as every other cross-zone jump.
// Esc, the backdrop, and ✕ all close.

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { readDoc } from "./api";
import ErrorBoundary from "./ErrorBoundary";
import { useNav } from "./nav";
import { preprocessLinks, splitFrontmatter, VaultImage } from "./VaultZone";

export default function DocPopup({ doc, onClose }: { doc: string; onClose: () => void }) {
  const nav = useNav();
  const [current, setCurrent] = useState(doc);
  const [stack, setStack] = useState<string[]>([]);
  const [raw, setRaw] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setRaw("");
    setError("");
    readDoc(current)
      .then(setRaw)
      .catch((e) => setError(String(e)));
  }, [current]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const open = (next: string) => {
    if (next === current) return;
    setStack((s) => [...s, current]);
    setCurrent(next);
  };
  const back = () => {
    setStack((s) => {
      const prev = s[s.length - 1];
      if (prev) setCurrent(prev);
      return s.slice(0, -1);
    });
  };

  const { body } = splitFrontmatter(raw);
  const name = current.split("/").pop()?.replace(/\.md$/, "") ?? current;
  const docDir = current.includes("/") ? current.slice(0, current.lastIndexOf("/")) : "";

  return createPortal(
    <div className="doc-popup-backdrop" onClick={onClose}>
      <div className="doc-popup bezel" role="dialog" aria-label={name}
           onClick={(e) => e.stopPropagation()}>
        <header className="doc-popup-head">
          {stack.length > 0 && (
            <button className="doc-popup-back" onClick={back} title="Back within the popup">◀</button>
          )}
          <strong className="doc-popup-name">{name}</strong>
          <span className="doc-popup-path">{current}</span>
          <a className="doc-popup-vault" href="#" title="Open this doc in the VAULT zone"
             onClick={(e) => { e.preventDefault(); nav.navigate({ zone: "vault", doc: current }); onClose(); }}>
            open in VAULT ↗
          </a>
          <button className="doc-popup-close" onClick={onClose} title="Close (Esc)">✕</button>
        </header>
        {error && <p className="surface-error">{error}</p>}
        {!error && (
          <ErrorBoundary resetKey={current}>
            <article className="vault-md doc-popup-body">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                urlTransform={(u) => u}
                components={{
                  a({ href, children }) {
                    if (href?.startsWith("wiki:")) {
                      const t = decodeURIComponent(href.slice(5)).split("#")[0].trim();
                      let target = t.endsWith(".md") ? t : `${t}.md`;
                      // bare wikilink names resolve against the CURRENT doc's dir — ticket
                      // cross-links are all siblings (ops/beads/), so sibling-first is correct
                      if (!target.includes("/") && docDir) target = `${docDir}/${target}`;
                      // in-popup swap: a linked ticket opens HERE (the local ◀ returns);
                      // the header's VAULT link is the committed-navigation path
                      return (
                        <a className="wikilink" href="#" title={`quick-look ${target}`}
                           onClick={(e) => { e.preventDefault(); open(target); }}>
                          {children}
                        </a>
                      );
                    }
                    return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
                  },
                  img({ src, alt }) {
                    return <VaultImage src={typeof src === "string" ? src : undefined}
                                       alt={typeof alt === "string" ? alt : ""} docDir={docDir} />;
                  },
                }}
              >
                {preprocessLinks(body)}
              </ReactMarkdown>
            </article>
          </ErrorBoundary>
        )}
      </div>
    </div>,
    document.body,
  );
}
