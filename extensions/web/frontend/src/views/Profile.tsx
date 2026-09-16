import { useEffect, useRef, useState } from "react";

import { IconPencil } from "@tabler/icons-react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Facts, Group } from "@/components/ui/facts";
import { Input } from "@/components/ui/field";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import {
  type NoticeState,
  OutcomeNotice,
  Panel,
  QUIET,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { type IntentOutcome, postAction, postIntent } from "@/lib/api";
import { MemberAvatar } from "@/lib/memberFace";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionView } from "@/lib/types";

const SET_PHOTO = "set_member_photo";
const CLEAR_PHOTO = "clear_member_photo";

/** The intent lane bounds a submit at 64 KB, which a camera's file blows through before it reaches
 *  the verb. 256 is also what the deploy stores, so nothing downstream resamples twice. */
const PICKED_DIMENSION = 256;
const PICKED_QUALITY = 0.9;
const PICKED_TYPE = "image/jpeg";
const ACCEPTED = "image/png,image/jpeg,image/gif,image/webp";

const UNREADABLE = "That file is not a picture the browser can read.";

/** A fact's value and the controls that change it, on one line. */
const FACT_ROW = "flex items-center gap-sm";

type ProfilePayload = {
  id: string;
  email: string;
  admin: boolean;
  name: string | null;
  drawn_name: string;
  photo_url: string | null;
  actions: ActionView[];
};

/** Canvas drops the file's EXIF on the way through, so the orientation tag and any location the
 *  camera wrote never leave the browser. */
async function pickedPhoto(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file);
  const side = Math.min(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = PICKED_DIMENSION;
  canvas.height = PICKED_DIMENSION;
  const ink = canvas.getContext("2d");
  if (!ink) throw new Error(UNREADABLE);
  ink.drawImage(
    bitmap,
    (bitmap.width - side) / 2,
    (bitmap.height - side) / 2,
    side,
    side,
    0,
    0,
    PICKED_DIMENSION,
    PICKED_DIMENSION,
  );
  bitmap.close();
  return canvas.toDataURL(PICKED_TYPE, PICKED_QUALITY).split(",")[1];
}

export function Profile() {
  const mainAgent = useMainAgent();
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<ProfilePayload>("/workspace/profile", reloads);
  const [name, setName] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const picker = useRef<HTMLInputElement>(null);

  const payload = state.phase === "ready" ? state.payload : null;

  useEffect(() => {
    if (payload && !editingName) setName(payload.name ?? "");
  }, [payload, editingName]);

  return (
    <Panel state={state} shape="form">
      {(ready) => {
        const act = (named: string): ActionView | undefined =>
          ready.actions.find((view) => view.name === named);

        function settle(outcome: IntentOutcome, told: string): void {
          setBusy(false);
          if (!outcome.applied) {
            setNotice(outcomeNotice(outcome));
            return;
          }
          setNotice(QUIET);
          setToast({ title: told });
          setReloads((count) => count + 1);
        }

        async function saveName() {
          if (busy || !mainAgent) return;
          setBusy(true);
          const trimmed = name.trim();
          const outcome = await postIntent(mainAgent.id, {
            verb: "apply",
            kind: "member_profile",
            name: ready.id,
            spec: { name: trimmed || null },
          });
          if (outcome.applied) setEditingName(false);
          settle(outcome, "Name saved.");
        }

        async function savePhoto(file: File) {
          const view = act(SET_PHOTO);
          if (busy || !mainAgent || !view) return;
          setBusy(true);
          let image: string;
          try {
            image = await pickedPhoto(file);
          } catch {
            setBusy(false);
            setNotice(outcomeNotice({ applied: false, message: UNREADABLE }));
            return;
          }
          settle(await postAction(mainAgent.id, view.call, { image }), "Photo saved.");
        }

        async function removePhoto() {
          const view = act(CLEAR_PHOTO);
          if (busy || !mainAgent || !view) return;
          setBusy(true);
          settle(await postAction(mainAgent.id, view.call, {}), "Photo removed.");
        }

        return (
          <>
            <Toast state={toast} onDone={() => setToast(SILENT)} position="surface" />
            <OutcomeNotice state={notice} />
            <Group title="Profile">
              <Facts
                rows={[
                  {
                    label: "Photo",
                    value: (
                      <div className={FACT_ROW}>
                        <MemberAvatar
                          face={{ email: ready.email, name: ready.drawn_name, photo_url: ready.photo_url }}
                          className="size-(--size-control)"
                        />
                        <input
                          ref={picker}
                          type="file"
                          accept={ACCEPTED}
                          className="sr-only"
                          onChange={(event) => {
                            const file = event.target.files?.[0];
                            event.target.value = "";
                            if (file) void savePhoto(file);
                          }}
                        />
                        <Button
                          variant="row"
                          busy={busy}
                          onClick={() => picker.current?.click()}
                        >
                          {ready.photo_url ? "Change" : "Add"}
                        </Button>
                        {ready.photo_url ? (
                          <ConfirmButton
                            verb="Remove"
                            variant="row"
                            busy={busy}
                            onClick={() => void removePhoto()}
                          />
                        ) : null}
                      </div>
                    ),
                  },
                  {
                    label: "Name",
                    value: editingName ? (
                      <div className={FACT_ROW}>
                        <Input
                          id="member-name"
                          aria-label="Name"
                          value={name}
                          placeholder={ready.drawn_name}
                          onChange={(event) => setName(event.target.value)}
                        />
                        <Button
                          type="button"
                          variant="send"
                          size="bar"
                          busy={busy}
                          onClick={() => void saveName()}
                        >
                          Save
                        </Button>
                      </div>
                    ) : (
                      <div className={FACT_ROW}>
                        <span>{ready.drawn_name}</span>
                        <Button
                          variant="quiet"
                          size="icon"
                          aria-label="Edit name"
                          onClick={() => setEditingName(true)}
                        >
                          <IconPencil aria-hidden />
                        </Button>
                      </div>
                    ),
                  },
                  { label: "Email", value: ready.email },
                  { label: "Role", value: ready.admin ? "Admin" : "Member" },
                ]}
              />
            </Group>
          </>
        );
      }}
    </Panel>
  );
}
