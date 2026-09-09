/** The models the composer offers, under the provider each belongs to. A model id is the deploy's
 *  own — the boot read names the served set, and the runtime routes and bills on the same id — so
 *  this table holds only what a member reads: the provider a model stands under, its brand mark,
 *  and its name. It is the portal's single home for those words; a view states a model by asking
 *  here rather than by carrying its own list. */

export type ModelProvider = { id: string; label: string; mark: string };

/** `family` names a model's version line: the models that differ from each other only by version
 *  number. A named or a lightweight variant — Sol, Luna, Flash, Mini — stands as its own family,
 *  because a member picks between those rather than between versions. Each family is held newest
 *  first, so the picker offers the head of each one. */
export type ModelChoice = { id: string; provider: string; family: string; label: string };

export type ModelGroup = { provider: ModelProvider; models: ModelChoice[] };

export const MODEL_PROVIDERS: readonly ModelProvider[] = [
  { id: "anthropic", label: "Claude", mark: "anthropic" },
  { id: "openai", label: "GPT", mark: "openai" },
  { id: "deepseek", label: "DeepSeek", mark: "deepseek" },
  { id: "zai", label: "GLM", mark: "zai" },
];

export const MODEL_CHOICES: readonly ModelChoice[] = [
  { id: "claude-opus-5", provider: "anthropic", family: "opus", label: "Opus 5" },
  { id: "claude-opus-4-8", provider: "anthropic", family: "opus", label: "Opus 4.8" },
  { id: "claude-opus-4-7", provider: "anthropic", family: "opus", label: "Opus 4.7" },
  { id: "claude-opus-4-6", provider: "anthropic", family: "opus", label: "Opus 4.6" },
  { id: "claude-sonnet-5", provider: "anthropic", family: "sonnet", label: "Sonnet 5" },
  { id: "claude-sonnet-4-6", provider: "anthropic", family: "sonnet", label: "Sonnet 4.6" },
  { id: "claude-haiku-4-5", provider: "anthropic", family: "haiku", label: "Haiku 4.5" },
  { id: "anthropic/claude-fable-5.1", provider: "anthropic", family: "fable", label: "Fable 5.1" },
  { id: "anthropic/claude-fable-5", provider: "anthropic", family: "fable", label: "Fable 5" },
  { id: "gpt-6-astra", provider: "openai", family: "gpt-astra", label: "GPT-6 Astra" },
  { id: "gpt-5.6-sol", provider: "openai", family: "gpt-sol", label: "GPT-5.6 Sol" },
  { id: "gpt-5.6-terra", provider: "openai", family: "gpt-terra", label: "GPT-5.6 Terra" },
  { id: "gpt-5.6-luna", provider: "openai", family: "gpt-luna", label: "GPT-5.6 Luna" },
  { id: "gpt-5.5", provider: "openai", family: "gpt", label: "GPT-5.5" },
  { id: "gpt-5.4", provider: "openai", family: "gpt", label: "GPT-5.4" },
  { id: "gpt-5.4-mini", provider: "openai", family: "gpt-mini", label: "GPT-5.4 Mini" },
  { id: "gpt-5.4-nano", provider: "openai", family: "gpt-nano", label: "GPT-5.4 Nano" },
  {
    id: "deepseek/deepseek-v4-flash",
    provider: "deepseek",
    family: "deepseek-flash",
    label: "DeepSeek V4 Flash",
  },
  { id: "z-ai/glm-5.3", provider: "zai", family: "glm", label: "GLM 5.3" },
  { id: "z-ai/glm-5.2", provider: "zai", family: "glm", label: "GLM 5.2" },
  { id: "z-ai/glm-5.3-flash", provider: "zai", family: "glm-flash", label: "GLM 5.3 Flash" },
];

const BY_ID = new Map(MODEL_CHOICES.map((choice) => [choice.id, choice]));

const PROVIDER_BY_ID = new Map(MODEL_PROVIDERS.map((provider) => [provider.id, provider]));

/** The sentinel an agent stores to run on the deploy's own choice rather than on a model of its
 *  own. It is served beside the concrete ids, and it stays the stored value: a screen that read it
 *  back as the model it resolves to would pin the agent to today's choice. */
export const AUTO_MODEL = "auto";

export const AUTO_LABEL = "Auto";

let served: readonly string[] | null = null;

/** The model ids this deploy serves, taken from the boot read. */
export function holdServedModels(ids: readonly string[]): void {
  served = ids;
}

/** Whether this deploy serves the `auto` sentinel, so the picker offers the way back to it. */
export function servesAuto(): boolean {
  return served === null || served.includes(AUTO_MODEL);
}

/** What a member reads for a model id: its name where the table names it, the id itself where the
 *  deploy serves one this portal has no word for. */
export function modelLabel(id: string): string {
  if (id === AUTO_MODEL) return AUTO_LABEL;
  return BY_ID.get(id)?.label ?? id;
}

/** The brand mark drawn beside a model, or null where the table names no provider for it. */
export function modelMark(id: string): string | null {
  const choice = BY_ID.get(id);
  return choice ? (PROVIDER_BY_ID.get(choice.provider)?.mark ?? null) : null;
}

/** An older version stays in the table — a member already on it still reads its name — and stays
 *  out of what the picker offers. */
function latestOfEachFamily(): ModelChoice[] {
  const offered = served;
  const taken = new Set<string>();
  return MODEL_CHOICES.filter((choice) => {
    if (offered !== null && !offered.includes(choice.id)) return false;
    if (taken.has(choice.family)) return false;
    taken.add(choice.family);
    return true;
  });
}

/** The menu the composer draws: a provider per row, in table order, holding the latest model of
 *  each family this deploy serves. A frame that has taken no boot read of its own offers every
 *  family's head, and a model the deploy refuses is refused where the choice is applied. */
export function modelMenu(): ModelGroup[] {
  const latest = latestOfEachFamily();
  const groups = MODEL_PROVIDERS.map((provider) => ({
    provider,
    models: latest.filter((choice) => choice.provider === provider.id),
  }));
  return groups.filter((group) => group.models.length > 0);
}
