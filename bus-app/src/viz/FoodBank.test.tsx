// The interactive food-bank twin's v2 pass: hover a card → the glance tip with
// the fit line + source links; click → the structured record with the links row. Fictional
// fixture — shipped fixtures never carry real places (the release scan's spirit).
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import FoodBank from "./FoodBank";

const fixture = {
  title: "Rivertown — restaurant bank",
  groups: [
    { key: "locked", label: "On the schedule" },
    { key: "candidates", label: "Candidates" },
  ],
  restaurants: [
    {
      name: "Herons Nest", group: "locked", area: "Old Pier", cuisine: "Seafood", price: "$$$",
      status: "booked", meals: ["dinner"], hostfit: "the group-dinner anchor — window tables",
      confirm: "reservation held", links: [{ label: "Yelp", url: "https://yelp.example/herons-nest" }],
    },
    {
      name: "Pasta Cellar", group: "candidates", area: "Mill Row", cuisine: "Italian", price: "$$",
      status: "verify", note: "handmade pasta room",
      links: [
        { label: "Yelp", url: "https://yelp.example/pasta-cellar" },
        { label: "site", url: "https://pastacellar.example" },
      ],
    },
  ],
};

describe("FoodBank v2", () => {
  it("renders grouped cards with status badges", () => {
    render(<FoodBank data={fixture} />);
    expect(screen.getByText("On the schedule")).toBeInTheDocument();
    expect(screen.getByText("Herons Nest")).toBeInTheDocument();
    expect(screen.getByText("BOOKED")).toBeInTheDocument();
  });

  it("hover shows the glance tip with the fit line + a source link", () => {
    const { container } = render(<FoodBank data={fixture} />);
    fireEvent.mouseMove(screen.getByText("Herons Nest").closest("button")!);
    const tip = container.querySelector(".viz-tip");
    expect(tip).not.toBeNull();
    expect(tip!.textContent).toContain("the group-dinner anchor");
    const link = tip!.querySelector("a.viz-tip-link") as HTMLAnchorElement;
    expect(link.href).toBe("https://yelp.example/herons-nest");
    expect(tip!.classList.contains("has-link")).toBe(true);
  });

  it("click opens the structured record with every source link", () => {
    const { container } = render(<FoodBank data={fixture} />);
    fireEvent.click(screen.getByText("Pasta Cellar").closest("button")!);
    const detail = container.querySelector(".viz-detail")!;
    expect(detail.textContent).toContain("handmade pasta room");
    const links = detail.querySelectorAll(".viz-detail-links a");
    expect(links.length).toBe(2);
    expect((links[1] as HTMLAnchorElement).href).toBe("https://pastacellar.example/");
    // external links must open out-of-app, never navigate the console
    links.forEach((a) => expect(a.getAttribute("target")).toBe("_blank"));
  });
});
