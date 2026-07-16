// The Settings modal: Obsidian-shaped rail+pane, reads from get_config, writes via
// the field-allowlisted commands. These pin the read model rendering + the connect flow's wiring.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Settings from "./Settings";
import { mockTauri } from "./test/mockTauri";

const CFG = {
  db_path: "/home/u/.local/state/harness/bus.db",
  bus_source: "/home/u/.local/state/harness/bus.db",
  producers: [{ id: "finance.pulse", label: "Finance pulse", cmd: "uv", args: [], cwd: "~" }],
  mode: "local",
  bus_url: null,
  bus_token_set: false,
  active_pack: null,
  tracker_path: "/home/u/projects/corpus",
  surfaces: [{ id: "finance.watch", label: "Watch digest", lane: "finance", cmd: "uv", args: [], cwd: "~" }],
  live_viz: [{ id: "beads.tree.live", label: "Beads board (LIVE)", lane: "beads" }],
  config_path: "/home/u/.config/harness/bus-app.json",
};

function props(overrides: Partial<Parameters<typeof Settings>[0]> = {}) {
  return {
    open: true, onClose: vi.fn(), version: "0.14.0",
    packs: [{ name: "demo-investor", path: "/packs/demo-investor", lanes: [] }],
    pack: "", onSelectPack: vi.fn(), onLoadPackDir: vi.fn(), onConfigChanged: vi.fn(),
    ...overrides,
  };
}

describe("Settings modal", () => {
  beforeEach(() => {
    mockTauri.reset();
    mockTauri.setValue("get_config", CFG);
    mockTauri.setValue("get_user_overlay", { text: "", source: "packaged template", path: null });
  });

  it("renders the grouped rail and the General pane (version + theme)", async () => {
    render(<Settings {...props()} />);
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    for (const tab of ["General", "Connection", "Weight packs", "Producers & surfaces"]) {
      expect(screen.getByRole("button", { name: tab })).toBeInTheDocument();
    }
    expect(screen.getByText("BUS-APP v0.14.0")).toBeInTheDocument();
    expect(screen.getByText("Theme")).toBeInTheDocument();
  });

  it("Connection: gates Connect/Test until url (+token for Test); connect calls set_bus_config", async () => {
    mockTauri.setValue("set_bus_config", { ...CFG, mode: "remote", bus_url: "http://host:8787", bus_token_set: true });
    const p = props();
    render(<Settings {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Connection" }));
    const connect = await screen.findByRole("button", { name: "Connect" });
    const test = screen.getByRole("button", { name: "Test" });
    expect(connect).toBeDisabled();
    expect(test).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("http://bus-host.tailnet.example:8787"),
      { target: { value: "http://host:8787" } });
    expect(connect).toBeEnabled();
    expect(test).toBeDisabled(); // test still needs the token typed
    fireEvent.change(screen.getByPlaceholderText("token"), { target: { value: "sekrit" } });
    expect(test).toBeEnabled();

    fireEvent.click(connect);
    await waitFor(() => expect(p.onConfigChanged).toHaveBeenCalled());
    const write = mockTauri.calls().find(([cmd]) => cmd === "set_bus_config");
    expect(write?.[1]).toMatchObject({ url: "http://host:8787", token: "sekrit" });
    expect(await screen.findByText(/demo pack cleared/)).toBeInTheDocument();
  });

  it("Weight packs: switching calls onSelectPack with the pack path", async () => {
    const p = props();
    render(<Settings {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Weight packs" }));
    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "/packs/demo-investor" } });
    expect(p.onSelectPack).toHaveBeenCalledWith("/packs/demo-investor");
  });

  it("Personal tabs render dynamically from the overlay yaml (phase 3)", async () => {
    mockTauri.setValue("get_user_overlay", {
      text: "finance:\n  global_settings:\n    brokerage: Example Broker\n    fund_holdings:\n      query: Acme Fund\ntravel:\n  global_settings:\n    home_airports: [AAA, BBB]\n",
      source: "user overlay", path: "/x/config/harness.yaml",
    });
    render(<Settings {...props()} />);
    // lane tabs appear under the Personal rail group
    fireEvent.click(await screen.findByRole("button", { name: "finance" }));
    expect(await screen.findByText("brokerage")).toBeInTheDocument();
    expect(screen.getByText("Example Broker")).toBeInTheDocument();
    expect(screen.getByText("fund_holdings.query")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "travel" }));
    expect(await screen.findByText("home_airports")).toBeInTheDocument();
    expect(screen.getByText("AAA, BBB")).toBeInTheDocument();
  });

  it("no overlay -> no Personal rail group (the packaged template renders nothing personal)", async () => {
    render(<Settings {...props()} />);
    await screen.findByRole("dialog", { name: "Settings" });
    expect(screen.queryByText("Personal")).not.toBeInTheDocument();
  });

  it("Producers & surfaces: renders the read-only rosters from config", async () => {
    render(<Settings {...props()} />);
    fireEvent.click(screen.getByRole("button", { name: "Producers & surfaces" }));
    expect(await screen.findByText("Finance pulse")).toBeInTheDocument();
    expect(screen.getByText("Watch digest")).toBeInTheDocument();
    expect(screen.getByText("Beads board (LIVE)")).toBeInTheDocument();
  });
});
