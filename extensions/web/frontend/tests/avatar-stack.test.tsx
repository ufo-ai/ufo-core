import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { AvatarGroupCount } from "@/components/ui/avatar";
import { AvatarStack, type AvatarStackPerson } from "@/components/ui/avatar-stack";

const OVERLAP = "[&>*:not(:first-child)]:-ml-2xs";
const RING = "border-[length:var(--spacing-hair)]";
const GAP = /(?<![\w-])gap-/;

const TEAM: AvatarStackPerson[] = [
  { name: "Rae Whitlock" },
  { name: "Cleo Marsh" },
  { name: "Tomas Ferrer" },
  { name: "Ines Okafor" },
  { name: "Ada Vance" },
];

const EVERYONE = TEAM.map((person) => person.name).join(", ");

/** jsdom fetches no image, so the loader is what stands in — Radix sets `src` the moment a source is given. */
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
  expect(screen.getByText("R")).toBeTruthy();
  expect(screen.getByText("C")).toBeTruthy();
  expect(screen.getByText("T")).toBeTruthy();
  expect(screen.getByText("+2")).toBeTruthy();
  expect(screen.queryByText("I")).toBeNull();
});

test("the overlap is the group's own, so a container spaces the stack without pulling it apart", () => {
  render(<AvatarStack people={TEAM} />);
  const stack = screen.getByRole("img", { name: EVERYONE });
  expect(GAP.test(stack.className)).toBe(false);
  expect(stack.className).toContain(OVERLAP);
  for (const circle of circlesOf(stack)) {
    expect(circle.className).toContain(RING);
    expect(circle.className).toContain("border-surface");
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

test("a company the theme carries no mark for is its member's initial", () => {
  render(<AvatarStack people={[{ name: "Dana Volk", company: "example.com" }]} />);
  expect(screen.getByText("D")).toBeTruthy();
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
  expect(screen.getByText("I")).toBeTruthy();
});

test("two members may share a name, and the stack draws both", () => {
  render(
    <AvatarStack people={[{ name: "Chris Bell" }, { name: "Chris Bell" }, { name: "Ada Lowe" }]} />,
  );
  expect(document.querySelectorAll("[data-slot=avatar]")).toHaveLength(3);
});

test("a member the workspace holds a picture for is drawn by it", async () => {
  const asked = pictures();
  render(
    <AvatarStack
      people={[
        { name: "Rae Whitlock", email: "rae@example.com", photo_url: "members/m1/photo?v=abc123" },
        { name: "Ada Lowe" },
      ]}
    />,
  );
  await vi.waitFor(() => expect(asked).toEqual(["/surface/web/members/m1/photo?v=abc123"]));
  expect(screen.getByText("A")).toBeTruthy();
});

test("a count closing a run of marks is that square, and one closing faces is a circle", () => {
  const { container, unmount } = render(<AvatarGroupCount count={3} />);
  const square = container.querySelector("[data-slot=avatar]") as HTMLElement;
  expect(square.textContent).toBe("+3");
  expect(square.className).toContain("rounded-key");
  expect(square.className).not.toContain("rounded-full");
  unmount();

  render(<AvatarStack people={TEAM} />);
  const circle = screen.getByText("+2").closest("[data-slot=avatar]") as HTMLElement;
  expect(circle.className).toContain("rounded-full");
  expect(circle.className).not.toContain("rounded-key");
});

test("the kit's stack answers to its own name, with one mark per face and the count", () => {
  const { container } = render(<AvatarStack people={TEAM} />);
  const stack = container.querySelector("[data-slot=avatar-stack]");
  expect(stack).toBeTruthy();
  expect(stack!.querySelectorAll("[data-slot=avatar]")).toHaveLength(4);
});
