import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { AvatarStack, type AvatarStackPerson } from "@/components/ui/avatar-stack";

const OVERLAP = "-ml-2xs";
const GAP = /(?<![\w-])gap-/;

const TEAM: AvatarStackPerson[] = [
  { name: "Rae Whitlock" },
  { name: "Cleo Marsh" },
  { name: "Tomas Ferrer" },
  { name: "Ines Okafor" },
  { name: "Ada Vance" },
];

const EVERYONE = TEAM.map((person) => person.name).join(", ");

/** Every URL the browser is asked for a picture. jsdom fetches no image, so the loader is what
 *  stands in — Radix sets `src` on one the moment a source is given. */
function pictures() {
  const asked: string[] = [];
  vi.stubGlobal(
    "Image",
    class {
      complete = false;
      naturalWidth = 0;
      addEventListener() {}
      removeEventListener() {}
      set src(url: string) {
        asked.push(url);
      }
    },
  );
  return asked;
}

const circlesOf = (stack: HTMLElement) => [...stack.querySelectorAll("[data-slot=avatar]")];

test("the stack draws three faces and states the rest as a count", () => {
  render(<AvatarStack people={TEAM} />);
  const stack = screen.getByRole("img", { name: EVERYONE });
  expect(circlesOf(stack).length).toBe(4);
  expect(screen.getByText("RW")).toBeTruthy();
  expect(screen.getByText("CM")).toBeTruthy();
  expect(screen.getByText("TF")).toBeTruthy();
  expect(screen.getByText("+2")).toBeTruthy();
  expect(screen.queryByText("IO")).toBeNull();
});

test("the overlap is the stack's own, spelled on every circle after the first", () => {
  render(<AvatarStack people={TEAM} />);
  const stack = screen.getByRole("img", { name: EVERYONE });
  expect(GAP.test(stack.className)).toBe(false);
  const circles = circlesOf(stack);
  expect(circles[0].className).not.toContain(OVERLAP);
  for (const circle of circles.slice(1)) {
    expect(circle.className).toContain(OVERLAP);
    expect(circle.className).toContain("border-card");
  }
});

test("the stack is one graphic naming everyone, so no set of initials is announced beside it", () => {
  render(<AvatarStack people={TEAM} />);
  const stack = screen.getByRole("img", { name: EVERYONE });
  const drawn = [...stack.querySelectorAll("[data-slot=avatar-fallback]")];
  expect(drawn.length).toBe(4);
  for (const fallback of drawn) expect(fallback.getAttribute("aria-hidden")).toBe("true");
  expect(stack.getAttribute("aria-label")).toContain("Ada Vance");
});

test("a single name gives one initial, and a stack of nobody draws nothing", () => {
  const { container } = render(<AvatarStack people={[{ name: "Prakash" }]} />);
  expect(screen.getByText("P")).toBeTruthy();
  expect(render(<AvatarStack people={[]} />).container.innerHTML).toBe("");
  expect(container.querySelectorAll("[data-slot=avatar]").length).toBe(1);
});

test("a company the theme carries a mark for is drawn from the theme", () => {
  render(<AvatarStack people={[{ name: "Dana Volk", company: "www.Stripe.com" }]} />);
  const mark = document.querySelector("[data-slot=avatar-fallback] > span") as HTMLElement;
  expect(mark.getAttribute("style")).toContain("--brand-stripe");
  expect(screen.queryByText("DV")).toBeNull();
});

test("a company the theme carries no mark for is its member's initials", () => {
  render(<AvatarStack people={[{ name: "Dana Volk", company: "example.com" }]} />);
  expect(screen.getByText("DV")).toBeTruthy();
  expect(document.querySelector("[data-slot=avatar-fallback] > span")).toBeNull();
});

test("a provider's mark is the circle the face beside it is", () => {
  render(
    <AvatarStack
      people={[{ name: "Dana Volk", company: "stripe.com" }, { name: "Rae Whitlock" }]}
    />,
  );
  const mark = document.querySelector("[data-slot=avatar-fallback] > span") as HTMLElement;
  expect(mark.className).toContain("rounded-full");
  expect(mark.className).toContain("size-full");
  const stack = screen.getByRole("img", { name: "Dana Volk, Rae Whitlock" });
  for (const circle of circlesOf(stack)) expect(circle.className).toContain("rounded-full");
});

// Who a workspace's members are is the workspace's. A picture asked of a host outside the product
// carries a member's name or company to that host on every page view, and the portal's own policy
// names no picture host — so the stack asks nothing of anyone, whatever a person carries.
test("no picture is asked of any host, whatever a person carries", async () => {
  const asked = pictures();
  render(
    <AvatarStack
      people={[
        { name: "Ines Okafor", company: "example.com" },
        { name: "Ola Ruiz", company: "stripe.com" },
        { name: "Ada Lowe" },
      ]}
    />,
  );
  await new Promise((settle) => setTimeout(settle, 20));
  expect(asked).toEqual([]);
  expect(document.querySelector("img")).toBeNull();
  expect(screen.getByText("IO")).toBeTruthy();
});

test("two members may share a name, and the stack draws both", () => {
  render(
    <AvatarStack people={[{ name: "Chris Bell" }, { name: "Chris Bell" }, { name: "Ada Lowe" }]} />,
  );
  expect(document.querySelectorAll("[data-slot=avatar]")).toHaveLength(3);
});
