import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { MessageLog, TranscriptScroll } from "@/kernel/messages";
import type { ChatFile } from "@/lib/types";

const DESKTOP: ChatFile = {
  filename: "application-preview-desktop.png",
  url: "/dl/application-preview-desktop.png",
  size_bytes: 36_779,
  preview_url: "https://web/artifacts/preview/desktop.png?token=signed",
  media_type: "image/png",
};
const PHONE: ChatFile = {
  filename: "application-preview-phone.png",
  url: "/dl/application-preview-phone.png",
  size_bytes: 26_359,
  preview_url: "https://web/artifacts/preview/phone.png?token=signed",
  media_type: "image/png",
};

function shared(files: ChatFile[]) {
  return render(
    <TranscriptScroll>
      <MessageLog messages={[{ role: "assistant", text: "Here is the design.", files }]} />
    </TranscriptScroll>,
  );
}

/** jsdom decodes no image, so the size a magnification is a multiple of is stated by hand and the
 *  load the viewer reads it on is fired by hand. */
function loaded(image: HTMLElement, naturalWidth: number) {
  Object.defineProperty(image, "naturalWidth", { value: naturalWidth, configurable: true });
  fireEvent.load(image);
}

test("a picture opens at the size of the window and zooms to its own pixels", async () => {
  shared([DESKTOP]);
  await userEvent.click(screen.getByRole("button", { name: DESKTOP.filename }));

  const viewer = await screen.findByRole("dialog", { name: DESKTOP.filename });
  expect(within(viewer).getByText("Fit")).toBeTruthy();
  const picture = within(viewer).getByRole("img", { name: DESKTOP.filename });
  expect(picture.getAttribute("src")).toBe(DESKTOP.preview_url);
  loaded(picture, 1280);

  // Fitted, the picture is bounded by the window and the stage does not scroll: there is nothing
  // outside the box to reach.
  expect(picture.style.width).toBe("");
  expect(picture.className).toContain("max-w-full");
  const stage = viewer.querySelector("[data-slot=lightbox-stage]");
  expect(stage?.className).toContain("overflow-hidden");

  await userEvent.click(within(viewer).getByRole("button", { name: "Zoom in" }));
  expect(within(viewer).getByText("100%")).toBeTruthy();
  expect(picture.style.width).toBe("1280px");
  expect(picture.className).toContain("max-w-none");
  expect(viewer.querySelector("[data-slot=lightbox-stage]")?.className).toContain("overflow-auto");

  await userEvent.click(within(viewer).getByRole("button", { name: "Zoom in" }));
  expect(within(viewer).getByText("200%")).toBeTruthy();
  expect(picture.style.width).toBe("2560px");

  await userEvent.click(within(viewer).getByRole("button", { name: "Zoom out" }));
  await userEvent.click(within(viewer).getByRole("button", { name: "Zoom out" }));
  expect(within(viewer).getByText("Fit")).toBeTruthy();
  expect(picture.style.width).toBe("");
});

test("a picture taller than the window is bounded by the stage", async () => {
  // jsdom computes no layout, so the bound is read where it is written: fitted, the picture is
  // bounded by a percentage of the button it sits in, and a percentage bounds nothing unless the
  // button is the stage's own height. A phone shot is taller than the stage after width fitting,
  // and the fitted stage does not scroll, so a button left at the picture's height cuts it off.
  shared([PHONE]);
  await userEvent.click(screen.getByRole("button", { name: PHONE.filename }));

  const viewer = await screen.findByRole("dialog", { name: PHONE.filename });
  const fitted = within(viewer).getByRole("button", { name: "Show at full size" });
  expect(fitted.classList.contains("h-full")).toBe(true);
  expect(within(viewer).getByRole("img", { name: PHONE.filename }).className).toContain(
    "max-h-full",
  );

  // Above `Fit` the picture is drawn at its own pixels and the stage scrolls to it, so the button
  // is sized by the picture again.
  loaded(within(viewer).getByRole("img", { name: PHONE.filename }), 390);
  await userEvent.click(within(viewer).getByRole("button", { name: "Zoom in" }));
  const held = within(viewer).getByRole("button", { name: "Fit to window" });
  expect(held.className).toContain("shrink-0");
  expect(held.classList.contains("h-full")).toBe(false);
});

test("pressing the picture itself cycles the two sizes a member reads", async () => {
  shared([DESKTOP]);
  await userEvent.click(screen.getByRole("button", { name: DESKTOP.filename }));
  const viewer = await screen.findByRole("dialog", { name: DESKTOP.filename });
  const picture = within(viewer).getByRole("img", { name: DESKTOP.filename });
  loaded(picture, 1280);

  await userEvent.click(within(viewer).getByRole("button", { name: "Show at full size" }));
  expect(picture.style.width).toBe("1280px");
  await userEvent.click(within(viewer).getByRole("button", { name: "Fit to window" }));
  expect(picture.style.width).toBe("");
});

test("the pictures one turn shared open as one set", async () => {
  shared([DESKTOP, PHONE]);
  await userEvent.click(screen.getByRole("button", { name: PHONE.filename }));

  const viewer = await screen.findByRole("dialog", { name: PHONE.filename });
  expect(within(viewer).getByText("2 / 2")).toBeTruthy();
  expect(within(viewer).getByRole("button", { name: "Next picture" }).hasAttribute("disabled")).toBe(
    true,
  );

  await userEvent.click(within(viewer).getByRole("button", { name: "Previous picture" }));
  expect(await screen.findByRole("dialog", { name: DESKTOP.filename })).toBeTruthy();
  expect(screen.getByText("1 / 2")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Previous picture" }).hasAttribute("disabled")).toBe(
    true,
  );
  expect(screen.getByRole("img", { name: DESKTOP.filename }).getAttribute("src")).toBe(
    DESKTOP.preview_url,
  );
});

test("a step to the next picture reads it at the size of the window", async () => {
  shared([DESKTOP, PHONE]);
  await userEvent.click(screen.getByRole("button", { name: DESKTOP.filename }));
  const viewer = await screen.findByRole("dialog", { name: DESKTOP.filename });
  loaded(within(viewer).getByRole("img", { name: DESKTOP.filename }), 1280);
  await userEvent.click(within(viewer).getByRole("button", { name: "Zoom in" }));
  expect(screen.getByText("100%")).toBeTruthy();

  await userEvent.click(within(viewer).getByRole("button", { name: "Next picture" }));
  expect(await screen.findByRole("dialog", { name: PHONE.filename })).toBeTruthy();
  expect(screen.getByText("Fit")).toBeTruthy();
  expect(screen.getByRole("img", { name: PHONE.filename }).style.width).toBe("");
});

test("a picture the browser already holds still zooms to its own pixels", async () => {
  // The second time a member opens one, the bytes are cached: the element completes before it
  // carries a load handler, so no load event arrives and only `complete` says how wide it is.
  Object.defineProperty(HTMLImageElement.prototype, "complete", { value: true, configurable: true });
  Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", {
    value: 1280,
    configurable: true,
  });
  try {
    shared([DESKTOP]);
    await userEvent.click(screen.getByRole("button", { name: DESKTOP.filename }));
    const viewer = await screen.findByRole("dialog", { name: DESKTOP.filename });
    await userEvent.click(within(viewer).getByRole("button", { name: "Zoom in" }));
    expect(within(viewer).getByText("100%")).toBeTruthy();
    expect(
      within(viewer).getAllByRole("img", { name: DESKTOP.filename }).map((held) => held.style.width),
    ).toEqual(["1280px"]);
  } finally {
    Reflect.deleteProperty(HTMLImageElement.prototype, "complete");
    Reflect.deleteProperty(HTMLImageElement.prototype, "naturalWidth");
  }
});

test("a cached picture stepped to still zooms to its own pixels", async () => {
  // The phone shot beside the desktop one is cached too, and by the time it is stepped to the
  // viewer is already mounted — so it attaches in the same commit whose effects reset the step.
  Object.defineProperty(HTMLImageElement.prototype, "complete", { value: true, configurable: true });
  Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", {
    get(this: HTMLImageElement) {
      return this.getAttribute("src") === PHONE.preview_url ? 390 : 1280;
    },
    configurable: true,
  });
  try {
    shared([DESKTOP, PHONE]);
    await userEvent.click(screen.getByRole("button", { name: DESKTOP.filename }));
    const viewer = await screen.findByRole("dialog", { name: DESKTOP.filename });
    await userEvent.click(within(viewer).getByRole("button", { name: "Next picture" }));
    await screen.findByRole("dialog", { name: PHONE.filename });
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByRole("img", { name: PHONE.filename }).style.width).toBe("390px");
  } finally {
    Reflect.deleteProperty(HTMLImageElement.prototype, "complete");
    Reflect.deleteProperty(HTMLImageElement.prototype, "naturalWidth");
  }
});

test("a lone picture is shown without the controls that step between several", async () => {
  shared([DESKTOP]);
  await userEvent.click(screen.getByRole("button", { name: DESKTOP.filename }));
  const viewer = await screen.findByRole("dialog", { name: DESKTOP.filename });
  expect(within(viewer).queryByRole("button", { name: "Next picture" })).toBeNull();
  expect(within(viewer).queryByRole("button", { name: "Previous picture" })).toBeNull();
  expect(within(viewer).getByRole("link", { name: "Download" }).getAttribute("href")).toBe(
    DESKTOP.url,
  );
});
