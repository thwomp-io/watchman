// UpdatePill drives src/update.ts (the self-update seam) — so the tests fake THAT module, never the
// plugins' IPC shapes (same doctrine as mocking transport.ts's modules, one seam down).
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import UpdatePill from "./UpdatePill";
import * as update from "./update";

vi.mock("./update", () => ({
  checkForUpdate: vi.fn(),
  restartApp: vi.fn(),
  // the real classifier is a pure string-match; keep it live so error ROUTING is tested for real
  isSignatureError: (err: unknown) => /signature|minisign/i.test(String(err)),
}));

const checkForUpdate = vi.mocked(update.checkForUpdate);
const restartApp = vi.mocked(update.restartApp);

function available(overrides: Partial<update.AvailableUpdate> = {}): update.AvailableUpdate {
  return {
    version: "0.9.0",
    notes: "fixes",
    downloadAndInstall: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("UpdatePill", () => {
  it("starts as the idle affordance", () => {
    render(<UpdatePill />);
    expect(screen.getByRole("button", { name: "CHECK FOR UPDATES" })).toBeInTheDocument();
  });

  it("no update available → UP TO DATE, still re-checkable", async () => {
    checkForUpdate.mockResolvedValue(null);
    render(<UpdatePill />);
    fireEvent.click(screen.getByRole("button", { name: "CHECK FOR UPDATES" }));
    // sticky, and itself the re-check affordance
    const again = await screen.findByRole("button", { name: "UP TO DATE ✓" });
    fireEvent.click(again);
    expect(checkForUpdate).toHaveBeenCalledTimes(2);
  });

  it("update available → version + notes, then download → restart affordance", async () => {
    const upd = available();
    checkForUpdate.mockResolvedValue(upd);
    render(<UpdatePill />);
    fireEvent.click(screen.getByRole("button", { name: "CHECK FOR UPDATES" }));
    const note = await screen.findByText("v0.9.0 AVAILABLE");
    expect(note).toHaveAttribute("title", "fixes"); // release notes ride the tooltip (footer stays terse)
    fireEvent.click(screen.getByRole("button", { name: "DOWNLOAD" }));
    expect(upd.downloadAndInstall).toHaveBeenCalledTimes(1);
    fireEvent.click(await screen.findByRole("button", { name: "RESTART TO UPDATE" }));
    expect(restartApp).toHaveBeenCalledTimes(1);
  });

  it("download progress renders the cumulative percentage", async () => {
    // capture the progress callback and drive it mid-download (a never-resolving install keeps the
    // downloading state on screen)
    let report: ((f: number | null) => void) | undefined;
    const upd = available({
      downloadAndInstall: vi.fn((onProgress: (f: number | null) => void) => {
        report = onProgress;
        return new Promise<void>(() => {});
      }),
    });
    checkForUpdate.mockResolvedValue(upd);
    render(<UpdatePill />);
    fireEvent.click(screen.getByRole("button", { name: "CHECK FOR UPDATES" }));
    fireEvent.click(await screen.findByRole("button", { name: "DOWNLOAD" }));
    expect(await screen.findByText("DOWNLOADING…")).toBeInTheDocument(); // size unknown yet
    act(() => report!(0.42));
    expect(await screen.findByText("DOWNLOADING 42%")).toBeInTheDocument();
  });

  it("check failure (offline) is a quiet inline note with retry — never a modal", async () => {
    checkForUpdate.mockRejectedValue(new Error("error sending request"));
    render(<UpdatePill />);
    fireEvent.click(screen.getByRole("button", { name: "CHECK FOR UPDATES" }));
    expect(await screen.findByText("UPDATE CHECK FAILED — OFFLINE?")).toBeInTheDocument();
    // the retry path goes around again
    checkForUpdate.mockResolvedValue(null);
    fireEvent.click(screen.getByRole("button", { name: "RETRY" }));
    expect(await screen.findByRole("button", { name: "UP TO DATE ✓" })).toBeInTheDocument();
  });

  it("a refused signature reads as a refused install, not connectivity", async () => {
    const upd = available({
      downloadAndInstall: vi.fn().mockRejectedValue(new Error("invalid signature for the download")),
    });
    checkForUpdate.mockResolvedValue(upd);
    render(<UpdatePill />);
    fireEvent.click(screen.getByRole("button", { name: "CHECK FOR UPDATES" }));
    fireEvent.click(await screen.findByRole("button", { name: "DOWNLOAD" }));
    expect(await screen.findByText("SIGNATURE REJECTED — UPDATE NOT INSTALLED")).toBeInTheDocument();
  });
});
