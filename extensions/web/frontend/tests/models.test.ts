import { expect, test } from "vitest";

import { holdServedModels, modelLabel, modelMenu } from "@/lib/models";

test("the picker offers no DeepSeek model, and names the one a deploy serves", () => {
  holdServedModels(["auto", "claude-opus-5", "gpt-6-astra", "deepseek/deepseek-v4.1-flash"]);

  expect(modelMenu().map((group) => group.provider.id)).toEqual(["anthropic", "openai"]);
  expect(modelMenu().flatMap((group) => group.models.map((choice) => choice.id))).not.toContain(
    "deepseek/deepseek-v4.1-flash",
  );
  expect(modelLabel("deepseek/deepseek-v4.1-flash")).toBe("DeepSeek V4.1 Flash");
});
