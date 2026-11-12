import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import FailureNote, { toFailure } from "./FailureNote";
import { InvokeError } from "./transport";

describe("FailureNote", () => {
  it("shows the kind, the sentence, the hint, a retry and the raw output", () => {
    const onRetry = vi.fn();
    const failure = toFailure(new InvokeError("nasdaq: HTTP 403 for ABC", 400, "source", "Try again in a minute.", "raw stderr"));
    render(<FailureNote failure={failure} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("A data source didn’t answer");
    expect(screen.getByText("nasdaq: HTTP 403 for ABC")).toBeInTheDocument();
    expect(screen.getByText("Try again in a minute.")).toBeInTheDocument();
    expect(screen.getByText("raw stderr")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("reduces anything else that was thrown to its message, with no hint, retry or details", () => {
    render(<FailureNote failure={toFailure(new Error("bad shape"))} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn’t load this");
    expect(screen.getByText("bad shape")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText("Details")).toBeNull();
  });
});
