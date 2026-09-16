import { IconCheck } from "@tabler/icons-react";
import { useState } from "react";

import { ToggleGroupItem, ToggleGroupOne } from "@/components/ui/toggle-group";
import {
  OutcomeNotice,
  Panel,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
  type NoticeState,
} from "@/kernel/panel";
import { cn } from "@/lib/cn";
import { postAction } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionView } from "@/lib/types";

const PRODUCT_NEWS = "product_news";
const FOUNDER_UPDATES = "founder_updates";
const SET_PRODUCT_EMAIL = "set_product_email";

const TITLE = "Email";
const NOTE =
  "This is your own setting, wherever you use ufo. It is not the workspace's.";

const DRAWN: Record<string, { label: string; note: string }> = {
  [PRODUCT_NEWS]: {
    label: "Product email",
    note: "Reminders about a workspace you were added to and have not set up.",
  },
  [FOUNDER_UPDATES]: {
    label: "Founder updates",
    note: "Mail from the founders about what ufo is doing.",
  },
};

const FROM_THE_EMAIL = "Use the unsubscribe link in the email to change this.";

const NOTICES_LABEL = "Workspace notices";
const NOTICES_NOTE =
  "What a workspace is doing with your money and your access. These always arrive.";
const ALWAYS = "Always on";

const ON = "On";
const OFF = "Off";

const NOTHING_TO_SET =
  "This deploy sends no email, so there is nothing to set.";

type Topic = { topic: string; receiving: boolean };
type Held = { address: string; topics: Topic[]; actions: ActionView[] };

export function WorkspaceEmail() {
  const agent = useMainAgent();
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [applying, setApplying] = useState(false);
  const state = usePanelRead<Held>("/workspace/email", reloads);

  return (
    <Panel
      state={state}
      shape="form"
      empty={(held) => (held.topics.length ? null : NOTHING_TO_SET)}
    >
      {(held) => {
        const act = held.actions.find(
          (entry) => entry.name === SET_PRODUCT_EMAIL,
        );
        const set = async (receiving: boolean) => {
          if (!agent || !act || applying) return;
          setApplying(true);
          const outcome = await postAction(agent.id, act.call, { receiving });
          setNotice(outcomeNotice(outcome));
          setApplying(false);
          if (outcome.applied) setReloads((count) => count + 1);
        };
        return (
          <div className="flex w-full flex-col gap-4xl">
            <Section title={TITLE} note={NOTE}>
              <p className="m-0 text-label text-ink-soft">{held.address}</p>
            </Section>
            {held.topics.map((entry) => (
              <Section
                key={entry.topic}
                title={DRAWN[entry.topic]?.label ?? entry.topic}
                note={DRAWN[entry.topic]?.note ?? ""}
              >
                {entry.topic === PRODUCT_NEWS ? (
                  <ToggleGroupOne
                    className="flex flex-col gap-2xs"
                    value={entry.receiving ? ON : OFF}
                    onValueChange={(picked) => {
                      if (picked) void set(picked === ON);
                    }}
                  >
                    {[ON, OFF].map((choice) => (
                      <ToggleGroupItem
                        key={choice}
                        value={choice}
                        disabled={applying || !act || !agent}
                        className={cn(
                          "group flex h-10 w-full items-center justify-between",
                          "rounded-(--radius-answer) border-0 bg-surface px-2xl text-start",
                          "text-label text-ink hover:bg-fill-strong",
                          "focus-visible:outline-2 focus-visible:outline-offset-2",
                          "focus-visible:outline-ink",
                        )}
                      >
                        {choice}
                        <IconCheck
                          className={cn(
                            "size-(--size-glyph) shrink-0 text-ink-soft opacity-0",
                            "group-data-[state=on]:opacity-100",
                          )}
                          aria-hidden
                        />
                      </ToggleGroupItem>
                    ))}
                  </ToggleGroupOne>
                ) : (
                  <p className="m-0 text-label text-ink-soft">
                    {entry.receiving ? ON : OFF}. {FROM_THE_EMAIL}
                  </p>
                )}
              </Section>
            ))}
            <Section title={NOTICES_LABEL} note={NOTICES_NOTE}>
              <p className="m-0 text-label text-ink-soft">{ALWAYS}</p>
            </Section>
            <OutcomeNotice state={notice} />
          </div>
        );
      }}
    </Panel>
  );
}
