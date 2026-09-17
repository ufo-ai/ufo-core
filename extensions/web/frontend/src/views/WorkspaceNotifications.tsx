import { useEffect, useState } from "react";

import { Facts, Group } from "@/components/ui/facts";
import { Checkbox } from "@/components/ui/field";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import {
  OutcomeNotice,
  Panel,
  QUIET,
  outcomeNotice,
  usePanelRead,
  type NoticeState,
} from "@/kernel/panel";
import { postAction } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionView } from "@/lib/types";

const PRODUCT_NEWS = "product_news";
const SET_PRODUCT_EMAIL = "set_product_email";

type Topic = { topic: string; receiving: boolean };
type Held = { address: string; topics: Topic[]; actions: ActionView[] };

export function WorkspaceNotifications() {
  const agent = useMainAgent();
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const [applying, setApplying] = useState(false);
  const [productEmail, setProductEmail] = useState<boolean | null>(null);
  const state = usePanelRead<Held>("/workspace/email", reloads);
  const stored = state.phase === "ready"
    ? state.payload.topics.find((entry) => entry.topic === PRODUCT_NEWS)?.receiving
    : undefined;

  useEffect(() => {
    if (stored === productEmail) setProductEmail(null);
  }, [stored, productEmail]);

  return (
    <Panel state={state} shape="form" empty={(held) => held.topics.length ? null : "Email is not available."}>
      {(held) => {
        const act = held.actions.find((entry) => entry.name === SET_PRODUCT_EMAIL);
        async function save(receiving: boolean) {
          if (!agent || !act || applying) return;
          setApplying(true);
          setProductEmail(receiving);
          const outcome = await postAction(agent.id, act.call, { receiving });
          setApplying(false);
          if (!outcome.applied) {
            setProductEmail(null);
            setNotice(outcomeNotice(outcome));
            return;
          }
          setNotice(QUIET);
          setToast({ title: "Notification preference saved." });
          setReloads((count) => count + 1);
        }
        return (
          <>
            <Toast state={toast} onDone={() => setToast(SILENT)} position="surface" />
            <OutcomeNotice state={notice} />
            <Group title="Email">
              <Facts rows={[
                { label: "Send to", value: held.address },
                ...held.topics.filter((entry) => entry.topic === PRODUCT_NEWS).map((entry) => ({
                  label: "Product email",
                  value: (
                    <Checkbox
                      aria-label="Product email"
                      checked={productEmail ?? entry.receiving}
                      disabled={applying || !act || !agent}
                      onChange={(event) => void save(event.target.checked)}
                    />
                  ),
                })),
                { label: "Account and billing", value: "Always on" },
              ]} />
            </Group>
          </>
        );
      }}
    </Panel>
  );
}
