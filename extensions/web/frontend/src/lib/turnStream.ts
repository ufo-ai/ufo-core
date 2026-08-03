import { BASE } from "@/lib/api";
import { money } from "@/lib/money";
import { chatState, liveTurn, updateChat, type LiveTurn, type ToolEvent } from "@/lib/chatStore";
import type { ChatFile, ChatQuestion } from "@/lib/types";

const MAX_STREAM_RETRIES = 5;

export function eventLabel(event: ToolEvent, phase: "active" | "done"): string {
  if (event.description) return event.description;
  if (event.kind === "skill") {
    return (phase === "active" ? "Loading skill" : "Loaded skill") + " · " + event.name;
  }
  return event.preview ? event.name + " " + event.preview : event.name;
}

export function streamTurn(agentId: string, turnId: string, answering: boolean): EventSource {
  const source = new EventSource(BASE + "/turns/" + turnId + "/stream");
  let sawFiles = false;
  let stalled = 0;

  const onLive = (change: (live: LiveTurn) => LiveTurn) =>
    updateChat(agentId, (state) => ({ ...state, live: change(state.live ?? liveTurn()) }));

  const record = () =>
    updateChat(agentId, (state) => {
      const live = state.live;
      if (!live || !live.text) return state;
      return {
        ...state,
        messages: (state.messages ?? []).concat({
          role: "assistant",
          text: live.text,
          ...(live.meta ? { meta: live.meta } : {}),
          ...(live.files ? { files: live.files } : {}),
          ...(live.connectUrl ? { connectUrl: live.connectUrl } : {}),
          ...(live.events.length ? { events: live.events } : {}),
        }),
      };
    });

  const close = () => {
    source.close();
    updateChat(agentId, (state) => ({ ...state, busy: false, live: null }));
  };

  source.addEventListener("open", () => {
    stalled = 0;
  });

  source.onmessage = (event) => {
    const chunk = JSON.parse(event.data).text as string;
    onLive((live) => ({ ...live, text: live.text + chunk }));
  };

  source.addEventListener("files", (event) => {
    const files = JSON.parse((event as MessageEvent).data).files as ChatFile[];
    sawFiles = true;
    updateChat(agentId, (state) => ({
      ...state,
      handoffs: { ...state.handoffs, files },
      live: { ...(state.live ?? liveTurn()), files },
    }));
  });

  source.addEventListener("credentials", (event) => {
    const credentials = JSON.parse((event as MessageEvent).data);
    updateChat(agentId, (state) => ({
      ...state,
      handoffs: { ...state.handoffs, credentials },
    }));
  });

  source.addEventListener("tool", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    const entry: ToolEvent = {
      kind: "tool",
      name: frame.tool,
      preview: frame.preview ?? "",
      description: frame.description ?? "",
    };
    onLive((live) => ({
      ...live,
      events: live.events.concat(entry),
      activity: eventLabel(entry, "active"),
    }));
  });

  source.addEventListener("skill", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    const entry: ToolEvent = { kind: "skill", name: frame.skill, preview: "", description: "" };
    onLive((live) => ({
      ...live,
      events: live.events.concat(entry),
      activity: eventLabel(entry, "active"),
    }));
  });

  source.addEventListener("cost", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    onLive((live) => ({
      ...live,
      meter: frame.tokens + " tok · " + money(frame.cost_micro_usd),
    }));
  });

  source.addEventListener("connect", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    onLive((live) => ({ ...live, connectUrl: frame.url }));
  });

  source.addEventListener("connect_error", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    onLive((live) => ({ ...live, activity: frame.message }));
  });

  source.addEventListener("terminal", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    updateChat(agentId, (state) => {
      const live = state.live ?? liveTurn();
      const handoffs = { ...state.handoffs };
      let text = live.text;
      let meta = live.meta;
      if (frame.status === "done") {
        if (frame.text && !text) text = frame.text;
        meta = frame.model + " · " + frame.tokens + " tok · " + money(frame.cost_micro_usd);
        if (frame.question) {
          handoffs.question = { turn_id: turnId, ...frame.question };
        } else if (!answering) {
          handoffs.question = null;
        }
      } else {
        const fallback =
          frame.text ||
          "(" + frame.status + (frame.error_class ? ": " + frame.error_class : "") + ")";
        text = text ? text + "\n" + fallback : fallback;
        if (!answering) handoffs.question = null;
      }
      if (!sawFiles) handoffs.files = null;
      return { ...state, handoffs, live: { ...live, text, meta } };
    });
    record();
    close();
  });

  source.addEventListener("parked", (event) => {
    const message = JSON.parse((event as MessageEvent).data).message as string;
    onLive((live) => ({ ...live, text: live.text ? live.text + "\n" + message : message }));
    record();
    close();
  });

  source.onerror = () => {
    stalled += 1;
    if (source.readyState !== EventSource.CLOSED && stalled < MAX_STREAM_RETRIES) return;
    record();
    updateChat(agentId, (state) => ({
      ...state,
      messages: (state.messages ?? []).concat({
        role: "error",
        text: "Connection lost — reload to see the reply.",
      }),
    }));
    close();
  };

  return source;
}

export async function sendMessage(
  agentId: string,
  body: string | FormData,
  shown: string,
): Promise<void> {
  updateChat(agentId, (state) => ({
    ...state,
    busy: true,
    live: liveTurn(),
    messages: (state.messages ?? []).concat({ role: "user", text: shown }),
  }));
  let res: Response;
  try {
    res = await fetch(BASE + "/agents/" + agentId + "/chat", {
      method: "POST",
      body,
      credentials: "same-origin",
    });
  } catch {
    failTurn(agentId, "Network error — try again.");
    return;
  }
  if (!res.ok) {
    failTurn(agentId, "Error " + res.status + " — try again.");
    return;
  }
  let accepted: { turn_id: string };
  try {
    accepted = await res.json();
  } catch {
    failTurn(agentId, "Network error — try again.");
    return;
  }
  streamTurn(agentId, accepted.turn_id, false);
}

export async function answerQuestion(
  agentId: string,
  turnId: string,
  questionIndex: number,
  body: string,
): Promise<void> {
  const state = chatState(agentId);
  if (state.busy || state.messages === null) return;
  updateChat(agentId, (current) => ({ ...current, busy: true, live: liveTurn() }));
  let res: Response;
  try {
    res = await fetch(BASE + "/agents/" + agentId + "/chat", {
      method: "POST",
      body,
      credentials: "same-origin",
      headers: {
        "x-ufo-answer-turn": turnId,
        "x-ufo-answer-question": String(questionIndex),
      },
    });
  } catch {
    failTurn(agentId, "Network error — try again.");
    return;
  }
  if (!res.ok) {
    failTurn(agentId, "Error " + res.status + " — try again.");
    return;
  }
  let payload: { body?: string; turn_id: string };
  try {
    payload = await res.json();
  } catch {
    failTurn(agentId, "Network error — try again.");
    return;
  }
  const landed = payload.body || body;
  updateChat(agentId, (current) => ({
    ...current,
    messages: (current.messages ?? []).concat({ role: "user", text: landed }),
    handoffs: {
      ...current.handoffs,
      question: markAnswered(current.handoffs.question, questionIndex, turnId),
    },
  }));
  streamTurn(agentId, payload.turn_id, true);
}

function failTurn(agentId: string, message: string): void {
  updateChat(agentId, (state) => ({
    ...state,
    busy: false,
    live: null,
    messages: (state.messages ?? []).concat({ role: "error", text: message }),
  }));
}

export function markAnswered(
  question: ChatQuestion | null | undefined,
  index: number,
  turnId: string,
): ChatQuestion | null {
  if (!question || question.turn_id !== turnId) return question ?? null;
  const answered = (question.answered ?? []).concat(index);
  if (answered.length >= question.questions.length) return null;
  return { ...question, answered };
}
