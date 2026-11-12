// A failed read, shown the same way wherever one can happen: what kind of failure, one sentence,
// what to do about it, and the raw output folded away for whoever has to debug it.

import { InvokeError } from "./transport";
import type { Failure } from "./types";

const HEADLINE: Record<NonNullable<Failure["kind"]>, string> = {
  source: "A data source didn\u2019t answer",
  internal: "Something broke in harness",
  error: "Couldn\u2019t load this",
};

/** Normalize anything a promise can reject with into a Failure. */
export function toFailure(e: unknown): Failure {
  if (e instanceof InvokeError) return { message: e.message, kind: e.kind, hint: e.hint, detail: e.detail };
  return { message: e instanceof Error ? e.message : String(e) };
}

export default function FailureNote({ failure, onRetry }: { failure: Failure; onRetry?: () => void }) {
  return (
    <div className="failure" role="alert">
      <span className="section-label">{HEADLINE[failure.kind ?? "error"]}</span>
      <p className="failure-message">{failure.message}</p>
      {failure.hint && <p className="failure-hint">{failure.hint}</p>}
      {onRetry && <button className="failure-retry" onClick={onRetry}>Try again</button>}
      {failure.detail && (
        <details className="failure-detail">
          <summary>Details</summary>
          <pre>{failure.detail}</pre>
        </details>
      )}
    </div>
  );
}
