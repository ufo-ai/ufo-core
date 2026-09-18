import { afterEach, expect, test } from "vitest";

import { resetChatStore, updateChat } from "@/lib/chatStore";
import { SIMULATED_CONVERSATION } from "@/playground/simulator/scenarios";
import { installWire } from "@/playground/simulator/wire";

afterEach(() => {
  resetChatStore();
});

const transcript = async (): Promise<{ messages: { text: string }[] }> => {
  const answer = await fetch(
    "/agents/a/conversations/" + SIMULATED_CONVERSATION + "/transcript",
  );
  return answer.json();
};

/** A window focus resyncs the chat against this read, so answering it empty replaced a turn the
 *  member had just watched play with the blank state. */
test("a read of the transcript answers with the words the conversation holds", async () => {
  const restore = installWire();
  try {
    expect((await transcript()).messages).toEqual([]);

    updateChat(SIMULATED_CONVERSATION, (state) => ({
      ...state,
      messages: [{ role: "user", text: "what changed" }],
    }));

    expect((await transcript()).messages).toEqual([{ role: "user", text: "what changed" }]);
  } finally {
    restore();
  }
});
