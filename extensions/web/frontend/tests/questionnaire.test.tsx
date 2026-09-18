import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, TURN_ID, useStreamFake, wire } from "./harness";

beforeEach(() => {
  location.hash = "#/c/" + CONVO_ID;
  useStreamFake();
});

const ASKED = { question: "Which?", options: [{ label: "left" }, { label: "right" }] };

function asking(question: Record<string, unknown>, posts: RequestInit[] = []) {
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () =>
      json({
        messages: [
          { role: "assistant", text: "asking", question: { turn_id: TURN_ID, ...question } },
        ],
      }),
    "/slots": () => json({ slots: [] }),
    "/chat": (_url, init) => {
      posts.push(init ?? {});
      return json({ turn_id: TURN_ID, body: String(init?.body ?? "") });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  return posts;
}

const rowsOf = () =>
  Array.from(document.querySelectorAll("[data-slot=questionnaire-choices] > *"));

const keyOf = (row: Element) => row.querySelector("[data-slot$=shortcut]")?.textContent;

test("the ask is one card, headed by what it is about and marked at its trailing edge", async () => {
  asking({ title: "Create new app", icon: "rocket", questions: [ASKED] });

  const title = await screen.findByText("Create new app");
  expect(title.getAttribute("data-slot")).toBe("card-title");
  const header = title.closest("[data-slot=card-header]")!;
  expect(header.lastElementChild!.getAttribute("data-slot")).toBe("card-action");
  expect(header.querySelector("[data-slot=avatar-fallback] svg")).toBeTruthy();

  const card = header.closest("[data-slot=card]") as HTMLElement;
  expect(card.className).toContain("rounded-card");
  expect(card.className).toContain("border-edge");
  expect(within(card).getByRole("radio", { name: "left" })).toBeTruthy();
  expect(within(card).getByRole("button", { name: "Continue" })).toBeTruthy();
});

/** jsdom lays nothing out, so the class is the contract. */
test("an answer row shrinks inside the card rather than carrying past it", async () => {
  asking({
    title: "Pick one",
    questions: [
      {
        question: "Which?",
        options: [
          { label: "left", description: "a description far longer than the card is wide" },
          { label: "right" },
        ],
      },
    ],
  });

  const row = (await screen.findByRole("radio", { name: /left/ })).closest(
    "[data-slot=questionnaire-choice]",
  )!;
  expect(row.className).toContain("min-w-0");
  expect(document.querySelector("[data-slot=questionnaire-write]")!.className).toContain("min-w-0");
});

test("an answer stacks its description under its label, clamped until it is the chosen one", async () => {
  const wordy = "a description far longer than the card is wide";
  asking({
    title: "Pick one",
    questions: [
      {
        question: "Which?",
        options: [{ label: "left", description: wordy }, { label: "right" }],
      },
    ],
  });

  const description = await screen.findByText(wordy);
  expect(description.className).toContain("line-clamp-2");
  expect(description.className).not.toContain("truncate");
  expect(description.className).toContain(
    "group-data-checked/questionnaire-choice:line-clamp-none",
  );
  expect(description.closest("[data-slot=questionnaire-choice-label]")!.className).toContain(
    "flex-col",
  );
});

test("an ask carrying no mark is headed by its words alone", async () => {
  asking({ title: "Pick one", questions: [ASKED] });

  const header = (await screen.findByText("Pick one")).parentElement!;
  expect(header.querySelector("[data-slot=avatar]")).toBeNull();
  expect(screen.getByRole("radio", { name: "left" })).toBeTruthy();
});

test("every answer carries its key, and the row the member types into carries the last", async () => {
  asking({
    title: "Pick one",
    questions: [
      { question: "Which?", options: [{ label: "left" }, { label: "right" }, { label: "either" }] },
    ],
  });

  await screen.findByRole("radio", { name: "left" });
  const rows = rowsOf();
  expect(rows.map((row) => row.getAttribute("data-slot"))).toEqual([
    "questionnaire-choice",
    "questionnaire-choice",
    "questionnaire-choice",
    "questionnaire-write",
  ]);
  expect(rows.map(keyOf)).toEqual(["A", "B", "C", "D"]);
  expect(within(rows[3] as HTMLElement).getByRole("textbox", { name: "Which?" })).toBeTruthy();
});

test("a key is drawn at the leading edge of the row, over the control that carries the answer", async () => {
  asking({ title: "Pick one", questions: [ASKED] });

  await screen.findByRole("radio", { name: "left" });
  const row = rowsOf()[0];
  expect(Array.from(row.children).map((part) => part.getAttribute("data-slot"))).toEqual([
    "questionnaire-choice-input",
    "questionnaire-choice-shortcut",
    "questionnaire-choice-label",
  ]);
  const left = screen.getByRole("radio", { name: "left" }) as HTMLInputElement;
  expect(left.name).toBe("0");
  expect(left.value).toBe("left");
  expect(row.querySelector("[data-slot=questionnaire-choice-shortcut]")!.getAttribute("aria-hidden")).toBe(
    "true",
  );
});

test("a single-select question admits one answer and submits it", async () => {
  const posts = asking({ title: "Pick one", questions: [ASKED] });

  const left = (await screen.findByRole("radio", { name: "left" })) as HTMLInputElement;
  const right = screen.getByRole("radio", { name: "right" }) as HTMLInputElement;
  await userEvent.click(left);
  await userEvent.click(right);
  expect(left.checked).toBe(false);
  expect(right.checked).toBe(true);
  expect(right.closest("[data-slot=questionnaire-choice]")!.getAttribute("data-checked")).toBe("");

  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect(posts[0].body).toBe("right");
  const headers = posts[0].headers as Record<string, string>;
  expect(headers["x-ufo-answer-turn"]).toBe(TURN_ID);
  expect(headers["x-ufo-answer-question"]).toBe("0");
});

test("a multi-select question holds every answer the member presses", async () => {
  const posts = asking({
    title: "Pick any",
    questions: [{ ...ASKED, question: "Which ones?", multi_select: true }],
  });

  const left = (await screen.findByRole("checkbox", { name: "left" })) as HTMLInputElement;
  await userEvent.click(left);
  await userEvent.click(screen.getByRole("checkbox", { name: "right" }));
  expect(left.checked).toBe(true);

  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect(posts[0].body).toBe("left, right");
});

test("words in the last row are an answer, and they take the place of a choice", async () => {
  const posts = asking({ title: "Pick one", questions: [ASKED] });

  const right = (await screen.findByRole("radio", { name: "right" })) as HTMLInputElement;
  await userEvent.click(right);
  await userEvent.type(screen.getByRole("textbox", { name: "Which?" }), "neither");
  expect(right.checked).toBe(false);

  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect(posts[0].body).toBe("neither");
});

/** Gecko keeps an element out of its ancestor form for good once a `form` attribute has stood on it, so
 *  the member's words would never reach the answer. */
test("the row the member types into is never taken out of the form", async () => {
  asking({ title: "Pick one", questions: [ASKED] });

  const written = (await screen.findByRole("textbox", { name: "Which?" })) as HTMLInputElement;
  const form = document.querySelector("form[data-slot=questionnaire]") as HTMLFormElement;
  expect(written.hasAttribute("form")).toBe(false);
  expect(written.form).toBe(form);
  expect(written.getAttribute("name")).toBeNull();
  expect([...new FormData(form).keys()]).toEqual([]);

  await userEvent.type(written, "neither");

  expect(written.hasAttribute("form")).toBe(false);
  expect(written.form).toBe(form);
  expect(new FormData(form).getAll("0")).toEqual(["neither"]);
});

test("an answer already settled opens the form on it", async () => {
  const posts = asking({ title: "Pick one", questions: [{ ...ASKED, chosen: "right" }] });

  const right = (await screen.findByRole("radio", { name: "right" })) as HTMLInputElement;
  expect(right.checked).toBe(true);
  expect((screen.getByRole("radio", { name: "left" }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole("textbox", { name: "Which?" }) as HTMLInputElement).value).toBe("");

  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect(posts[0].body).toBe("right");
});

test("an answer no option holds opens in the row the member types into", async () => {
  const posts = asking({ title: "Pick one", questions: [{ ...ASKED, chosen: "somewhere else" }] });

  const written = (await screen.findByRole("textbox", { name: "Which?" })) as HTMLInputElement;
  expect(written.value).toBe("somewhere else");
  expect((screen.getByRole("radio", { name: "left" }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole("radio", { name: "right" }) as HTMLInputElement).checked).toBe(false);

  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect(posts[0].body).toBe("somewhere else");
});

test("an unanswered question is refused, and says what is missing", async () => {
  const posts = asking({ title: "Pick one", questions: [ASKED] });

  await userEvent.click(await screen.findByRole("button", { name: "Continue" }));

  expect(posts.length).toBe(0);
  const refusal = await screen.findByRole("alert");
  expect(refusal.textContent).toBe("Choose an answer or skip this question.");
});

test("the stepper states where the member is and moves both ways", async () => {
  asking({
    title: "Two things",
    questions: [
      { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
      { question: "Second?", options: [{ label: "gamma" }, { label: "delta" }] },
    ],
  });

  await screen.findByRole("radio", { name: "alpha" });
  const stepper = document.querySelector("[data-slot=questionnaire-stepper]")!;
  expect(stepper.textContent).toBe("1/2");

  await userEvent.click(screen.getByRole("radio", { name: "alpha" }));
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(stepper.textContent).toBe("2/2");
  expect(screen.getByRole("radio", { name: "gamma" })).toBeTruthy();
  expect(screen.queryByRole("radio", { name: "alpha" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(stepper.textContent).toBe("1/2");
  expect((screen.getByRole("radio", { name: "alpha" }) as HTMLInputElement).checked).toBe(true);
});

test("one question is asked without a stepper", async () => {
  asking({ title: "Pick one", questions: [ASKED] });

  await screen.findByRole("radio", { name: "left" });
  expect(document.querySelector("[data-slot=questionnaire-stepper]")).toBeNull();
});

test("a free-text-only question takes words rather than a choice", async () => {
  asking({ title: "Say it", questions: [{ ...ASKED, free_text_only: true }] });

  expect(await screen.findByRole("textbox", { name: "Which?" })).toBeTruthy();
  expect(screen.queryByRole("radio", { name: "left" })).toBeNull();
  expect(keyOf(rowsOf().at(-1)!)).toBe("A");
});

test("a question the form cannot hold says where the answer goes, with no controls", async () => {
  asking({ title: "Send it", questions: [{ ...ASKED, allow_attachments: true }] });

  expect(await screen.findByText("Which?")).toBeTruthy();
  expect(screen.queryByRole("radio", { name: "left" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Which?" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  expect(screen.getByText("Answer in the message box below.")).toBeTruthy();
});

test("a question with more options than fit offers none of them as a row", async () => {
  asking({
    title: "Pick one",
    questions: [
      {
        question: "Which one?",
        options: Array.from({ length: 11 }, (_, index) => ({ label: "option-" + index })),
      },
    ],
  });

  expect(await screen.findByText("Which one?")).toBeTruthy();
  expect(screen.queryByRole("radio", { name: "option-0" })).toBeNull();
  expect(screen.getByText("Answer in the message box below.")).toBeTruthy();
});

test("a chosen answer stands as chosen, and the run moves to the question after it", async () => {
  asking({
    title: "Two things",
    questions: [
      { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
      { question: "Second?", options: [{ label: "gamma" }, { label: "delta" }] },
    ],
  });

  const alpha = await screen.findByRole("radio", { name: "alpha" });
  await userEvent.click(alpha);
  expect((alpha as HTMLInputElement).checked).toBe(true);

  await waitFor(() => expect(screen.getByRole("radio", { name: "gamma" })).toBeTruthy());
  expect(document.querySelector("[data-slot=questionnaire-stepper]")!.textContent).toBe("2/2");
});

test("the last question waits on the member rather than moving past itself", async () => {
  asking({ title: "One thing", questions: [ASKED] });

  await userEvent.click(await screen.findByRole("radio", { name: "left" }));
  await new Promise((wake) => setTimeout(wake, 400));

  expect(screen.getByRole("radio", { name: "left" })).toBeTruthy();
  expect((screen.getByRole("radio", { name: "left" }) as HTMLInputElement).checked).toBe(true);
});

test("a question taking several answers waits while the member picks them", async () => {
  asking({
    title: "Two things",
    questions: [
      {
        question: "Which ones?",
        multi_select: true,
        options: [{ label: "alpha" }, { label: "beta" }],
      },
      { question: "Second?", options: [{ label: "gamma" }] },
    ],
  });

  await userEvent.click(await screen.findByRole("checkbox", { name: "alpha" }));
  await new Promise((wake) => setTimeout(wake, 400));

  expect(screen.getByRole("checkbox", { name: "beta" })).toBeTruthy();
  expect(document.querySelector("[data-slot=questionnaire-stepper]")!.textContent).toBe("1/2");
});

test("a question offering one answer draws no choice, and opens with it in the row", async () => {
  const posts = asking({
    title: "Create new app",
    questions: [
      { question: "What job is it for?", options: [{ label: "Watch x.com for AI keywords" }] },
    ],
  });

  const written = (await screen.findByRole("textbox", {
    name: "What job is it for?",
  })) as HTMLInputElement;
  expect(written.value).toBe("Watch x.com for AI keywords");
  expect(screen.queryByRole("radio")).toBeNull();
  expect(rowsOf().map((row) => row.getAttribute("data-slot"))).toEqual(["questionnaire-write"]);
  expect(keyOf(rowsOf()[0])).toBe("A");

  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect(posts[0].body).toBe("Watch x.com for AI keywords");
});

test("the run opens on the first question the turn does not already have the answer to", async () => {
  asking({
    title: "Create new app",
    questions: [
      {
        question: "What job is it for?",
        chosen: "Watch x.com for AI keywords",
        options: [{ label: "Watch x.com for AI keywords" }],
      },
      {
        question: "Where does the work live?",
        options: [{ label: "the shared inbox" }, { label: "the tracker" }],
      },
      { question: "Who sees it?", options: [{ label: "just me" }, { label: "the workspace" }] },
    ],
  });

  await screen.findByRole("radio", { name: "the shared inbox" });
  const stepper = document.querySelector("[data-slot=questionnaire-stepper]")!;
  expect(stepper.textContent).toBe("2/3");
  expect(screen.queryByRole("textbox", { name: "What job is it for?" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(stepper.textContent).toBe("1/3");
  const written = screen.getByRole("textbox", {
    name: "What job is it for?",
  }) as HTMLInputElement;
  expect(written.value).toBe("Watch x.com for AI keywords");
});

test("a pressed answer moves the run past a question the turn already has", async () => {
  asking({
    title: "Three things",
    questions: [
      { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
      {
        question: "Second?",
        chosen: "delta",
        options: [{ label: "gamma" }, { label: "delta" }],
      },
      { question: "Third?", options: [{ label: "epsilon" }, { label: "zeta" }] },
    ],
  });

  await userEvent.click(await screen.findByRole("radio", { name: "alpha" }));

  await waitFor(() => expect(screen.getByRole("radio", { name: "epsilon" })).toBeTruthy());
  expect(document.querySelector("[data-slot=questionnaire-stepper]")!.textContent).toBe("3/3");
});

test("an answer the run passed over is submitted with the rest", async () => {
  const posts = asking({
    title: "Two things",
    questions: [
      { question: "First?", chosen: "beta", options: [{ label: "alpha" }, { label: "beta" }] },
      { question: "Second?", options: [{ label: "gamma" }, { label: "delta" }] },
    ],
  });

  await userEvent.click(await screen.findByRole("radio", { name: "gamma" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(2));
  expect(posts.map((post) => post.body)).toEqual(["beta · First?", "gamma · Second?"]);
});

test("a question taking several answers is asked even with one of them chosen", async () => {
  asking({
    title: "Two things",
    questions: [
      {
        question: "Which ones?",
        multi_select: true,
        chosen: "alpha",
        options: [{ label: "alpha" }, { label: "beta" }],
      },
      { question: "Second?", options: [{ label: "gamma" }, { label: "delta" }] },
    ],
  });

  const alpha = (await screen.findByRole("checkbox", { name: "alpha" })) as HTMLInputElement;
  expect(alpha.checked).toBe(true);
  expect(document.querySelector("[data-slot=questionnaire-stepper]")!.textContent).toBe("1/2");
});

/** The arrow keys select each radio they pass and the browser fires a click for every one — detail 0,
 *  no pointer under it. `fireEvent.click` carries the same signature, so this is that click by proxy. */
test("a click no pointer pressed does not carry the member off the question", async () => {
  asking({
    title: "Two things",
    questions: [
      { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
      { question: "Second?", options: [{ label: "gamma" }] },
    ],
  });

  const alpha = await screen.findByRole("radio", { name: "alpha" });
  fireEvent.click(alpha.closest("[data-slot=questionnaire-choice]")!);
  await new Promise((wake) => setTimeout(wake, 400));

  expect(document.querySelector("[data-slot=questionnaire-stepper]")!.textContent).toBe("1/2");
});

test("a stepper act outranks a pressed answer's pending move", async () => {
  asking({
    title: "Two things",
    questions: [
      { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
      { question: "Second?", options: [{ label: "gamma" }, { label: "delta" }] },
    ],
  });

  await userEvent.click(await screen.findByRole("radio", { name: "alpha" }));
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await new Promise((wake) => setTimeout(wake, 400));

  expect(document.querySelector("[data-slot=questionnaire-stepper]")!.textContent).toBe("1/2");
});

test("a multi-select question is answered by its boxes alone", async () => {
  asking({
    title: "Pick any",
    questions: [{ ...ASKED, question: "Which ones?", multi_select: true }],
  });

  expect(await screen.findByRole("checkbox", { name: "left" })).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "Which ones?" })).toBeNull();
  expect(document.querySelector("[data-slot=questionnaire-write]")).toBeNull();
});

test("a selection the member did not press does not carry them off the question", async () => {
  asking({
    title: "Two things",
    questions: [
      { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
      { question: "Second?", options: [{ label: "gamma" }] },
    ],
  });

  const beta = (await screen.findByRole("radio", { name: "beta" })) as HTMLInputElement;
  fireEvent.change(beta, { target: { checked: true } });
  await new Promise((wake) => setTimeout(wake, 400));

  expect(screen.getByRole("radio", { name: "alpha" })).toBeTruthy();
  expect(document.querySelector("[data-slot=questionnaire-stepper]")!.textContent).toBe("1/2");
});

const CREDENTIAL = {
  sealed: "sealed-2f41",
  reason: "The deploy log is behind an API key.",
  prompts: [{ slot: "vercel_token", prompt: "Paste a token with read access to the project." }],
};

const ASKED_FOR = "Paste a token with read access to the project.";

function handing(
  credentials: Record<string, unknown>,
  stores: () => Response = () => new Response("", { status: 200 }),
) {
  const posted: string[] = [];
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () =>
      json({ messages: [{ role: "assistant", text: "asking" }], credentials }),
    "/slots": () => json({ slots: [] }),
    "/credentials$": (_url, init) => {
      posted.push(String(init?.body ?? ""));
      return stores();
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  return posted;
}

test("the credential a turn asks for is a row in a list, marked at its leading edge", async () => {
  handing(CREDENTIAL);

  const row = (await screen.findByText("vercel_token")).closest("[data-slot=item]") as HTMLElement;
  expect(row.tagName).toBe("LI");
  expect(row.parentElement!.getAttribute("data-slot")).toBe("item-group");
  expect(row.querySelector("[data-slot=item-media] [data-slot=mark]")).toBeTruthy();
  expect(within(row).getByText(ASKED_FOR)).toBeTruthy();
  expect(within(row).getByRole("button", { name: "Set credential" })).toBeTruthy();
});

/** `item.tsx` keeps one inset for a dense row, so two rows in one list line up; a second spelling of
 *  it set the two handoffs a turn draws on two different left edges. */
test("the connect row and the credential row take one row inset", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () =>
      json({
        messages: [
          { role: "assistant", text: "asking", connect: { turn: TURN_ID, label: "GitHub" } },
        ],
        credentials: CREDENTIAL,
      }),
    "/slots": () => json({ slots: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const connect = (await screen.findByText("Connect GitHub")).closest("a")!;
  const credential = screen.getByText("vercel_token").closest("[data-slot=item]")!;
  const inset = (node: Element) =>
    node.className
      .split(" ")
      .filter((name) => /^(gap|px|py)-/.test(name))
      .sort();
  expect(inset(connect)).toEqual(["gap-sm", "px-2xl", "py-sm"]);
  expect(inset(credential)).toEqual(inset(connect));
});

/** React mirrors a controlled `value` into the element's attribute, so the secret stood in the
 *  page's own markup. */
test("the secret box is named and masked, and its value never reaches the markup", async () => {
  handing(CREDENTIAL);

  const box = (await screen.findByLabelText(ASKED_FOR)) as HTMLInputElement;
  expect(box.getAttribute("type")).toBe("password");
  expect(box.getAttribute("name")).toBe("vercel_token");
  expect(box.getAttribute("autocomplete")).toBe("new-password");
  expect(box.getAttribute("placeholder")).toBe("Paste the key");

  await userEvent.type(box, "sk-live-secret");

  expect(box.value).toBe("sk-live-secret");
  expect(box.getAttribute("value")).toBeNull();
  expect(document.body.innerHTML).not.toContain("sk-live-secret");
});

test("the act waits on a value", async () => {
  handing(CREDENTIAL);

  const act = (await screen.findByRole("button", { name: "Set credential" })) as HTMLButtonElement;
  expect(act.disabled).toBe(true);

  await userEvent.type(screen.getByLabelText(ASKED_FOR), "sk-live-secret");

  expect(act.disabled).toBe(false);
});

test("a refusal lands on its own reserved line and leaves what was asked for standing", async () => {
  handing(CREDENTIAL, () => new Response("The key was refused.", { status: 400 }));

  const box = await screen.findByLabelText(ASKED_FOR);
  expect(screen.getByRole("alert").textContent).toBe("");
  expect(screen.getByRole("alert").className).toContain("min-h-lh");

  await userEvent.type(box, "sk-live-secret");
  await userEvent.click(screen.getByRole("button", { name: "Set credential" }));

  await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("The key was refused."));
  expect(screen.getByText(ASKED_FOR)).toBeTruthy();
  expect(screen.getByLabelText(ASKED_FOR)).toBeTruthy();
});

test("a store the network never carried says so in the same line", async () => {
  handing(CREDENTIAL, () => {
    throw new Error("offline");
  });

  await userEvent.type(await screen.findByLabelText(ASKED_FOR), "sk-live-secret");
  await userEvent.click(screen.getByRole("button", { name: "Set credential" }));

  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toBe("Network error — try again."),
  );
});

test("a stored credential marks the row and takes the box away", async () => {
  const posted = handing(CREDENTIAL);

  await userEvent.type(await screen.findByLabelText(ASKED_FOR), "sk-live-secret");
  await userEvent.click(screen.getByRole("button", { name: "Set credential" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toBe("sealed=sealed-2f41&slot=vercel_token&value=sk-live-secret");
  await waitFor(() => expect(screen.queryByLabelText(ASKED_FOR)).toBeNull());
  const row = screen.getByText("vercel_token").closest("[data-slot=item]") as HTMLElement;
  expect(within(row).getByText("Stored.")).toBeTruthy();
  expect(row.querySelector("[data-slot=item-actions] svg")).toBeTruthy();
});

test("a credential the workspace already holds opens marked", async () => {
  handing({ ...CREDENTIAL, prompts: [{ ...CREDENTIAL.prompts[0], stored: true }] });

  const row = (await screen.findByText("vercel_token")).closest("[data-slot=item]") as HTMLElement;
  expect(within(row).getByText("Stored.")).toBeTruthy();
  expect(row.querySelector("input")).toBeNull();
  expect(row.querySelector("[data-slot=item-actions] svg")).toBeTruthy();
});
