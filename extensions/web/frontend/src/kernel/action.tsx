import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { FormFromSchema, initialSpecValue, numeric, type SpecValue } from "@/kernel/form";
import { OutcomeNotice, QUIET, outcomeNotice, type NoticeState } from "@/kernel/panel";
import type { IntentOutcome } from "@/lib/api";
import type { ActionInput, ActionView, SpecSchema } from "@/lib/types";

function initialValues(
  schema: SpecSchema,
  shown: string[],
  initial: Record<string, SpecValue>,
): Record<string, SpecValue> {
  const properties = schema.properties ?? {};
  return Object.fromEntries(
    shown.map((name) => [name, initialSpecValue(properties[name], initial[name] ?? properties[name].default)]),
  );
}

function filled(schema: SpecSchema, shown: string[], values: Record<string, SpecValue>): boolean {
  return (schema.required ?? [])
    .filter((name) => shown.includes(name))
    .every((name) => {
      const value = values[name];
      return typeof value === "boolean" || String(value ?? "").trim() !== "";
    });
}

function actionBody(
  schema: SpecSchema,
  shown: string[],
  values: Record<string, SpecValue>,
  fixed: ActionInput,
): ActionInput {
  const properties = schema.properties ?? {};
  const body: ActionInput = {};
  for (const name of shown) {
    const value = values[name];
    if (typeof value === "boolean") {
      body[name] = value;
      continue;
    }
    const said = String(value ?? "").trim();
    if (!said) continue;
    body[name] = numeric(properties[name]) ? Number(said) : said;
  }
  return { ...body, ...fixed };
}

/** One action drawn as the form its schema states: a control per field, defaults standing, the
 *  label on the act that commits it. An action that declares no fields is the act alone.
 *
 *  An action carrying `confirm` commits on the second press, the way `ConfirmButton` does: the
 *  first press arms the act and states the sentence, and editing a field or leaving the act
 *  disarms it. The refusal or result the submit answers stands on the form in the outcome
 *  register every panel uses.
 *
 *  `initial` seeds fields the member is likely to keep — the name an app had, the statement being
 *  corrected. `fixed` pins fields the screen has already decided — the operation its one button
 *  stands for, the ref a correction names — so they never draw and ride the body as given.
 *
 *  `act` receives the input body alone — the caller addresses it with `view.call`. A caller
 *  showing another action mounts a new form (key it by the action's name). */
export function ActionForm({
  view,
  act,
  initial = {},
  fixed = {},
}: {
  view: ActionView;
  act: (input: ActionInput) => Promise<IntentOutcome>;
  initial?: Record<string, SpecValue>;
  fixed?: ActionInput;
}) {
  const shown = Object.keys(view.input_schema.properties ?? {}).filter((name) => !(name in fixed));
  const [values, setValues] = useState(() => initialValues(view.input_schema, shown, initial));
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const ready = filled(view.input_schema, shown, values);

  function edit(name: string, value: SpecValue) {
    setArmed(false);
    setValues((held) => ({ ...held, [name]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !ready) return;
    if (view.confirm && !armed) {
      setNotice(QUIET);
      setArmed(true);
      return;
    }
    setArmed(false);
    setBusy(true);
    const outcome = await act(actionBody(view.input_schema, shown, values, fixed));
    setBusy(false);
    setNotice(outcomeNotice(outcome));
  }

  return (
    <form aria-label={view.label} onSubmit={submit} className="flex flex-col gap-xl">
      <OutcomeNotice state={notice} />
      <FormFromSchema schema={view.input_schema} fields={shown} values={values} onChange={edit} />
      <div className="flex items-baseline justify-end gap-lg">
        {armed ? (
          <p role="status" className="m-0 text-label text-ink-soft">
            {view.confirm}
          </p>
        ) : null}
        <Button
          type="submit"
          variant="send"
          size="bar"
          busy={busy}
          disabled={!ready}
          onBlur={() => setArmed(false)}
        >
          {armed ? "Confirm " + view.label.toLowerCase() : view.label}
        </Button>
      </div>
    </form>
  );
}

function fielded(view: ActionView): boolean {
  return Object.keys(view.input_schema.properties ?? {}).length > 0;
}

/** The controls a read's projected actions draw, one act per view named by its label. An action
 *  that takes fields opens the form its schema states in a sheet; one that takes none is its form
 *  drawn in place — the act alone, with its confirm step and its outcome. `post` addresses the body
 *  on the lane the caller owns; an applied act closes its sheet and hands the caller the outcome,
 *  which is what the caller re-reads or announces on. */
export function ActionControls({
  views,
  post,
  onApplied,
}: {
  views: ActionView[];
  post: (view: ActionView, input: ActionInput) => Promise<IntentOutcome>;
  onApplied?: (view: ActionView, outcome: IntentOutcome) => void;
}) {
  const [open, setOpen] = useState<ActionView | null>(null);

  function acting(view: ActionView) {
    return async (input: ActionInput) => {
      const outcome = await post(view, input);
      if (outcome.applied) {
        setOpen(null);
        onApplied?.(view, outcome);
      }
      return outcome;
    };
  }

  return (
    <>
      {views.map((view) =>
        fielded(view) ? (
          <Button key={view.name} variant="send" size="bar" onClick={() => setOpen(view)}>
            {view.label}
          </Button>
        ) : (
          <ActionForm key={view.name} view={view} act={acting(view)} />
        ),
      )}
      {open ? (
        <Sheet open title={open.label} onClose={() => setOpen(null)}>
          <ActionForm key={open.name} view={open} act={acting(open)} />
        </Sheet>
      ) : null}
    </>
  );
}
