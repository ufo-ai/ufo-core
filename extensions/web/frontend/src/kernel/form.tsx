import { Field, Input, Label, Switch, Textarea } from "@/components/ui/field";
import {
  PLAIN_CONTROL,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/cn";
import type { SchemaProperty, SpecSchema } from "@/lib/types";

export type SpecValue = string | boolean;

function specType(prop: SchemaProperty): string {
  if (prop.type) return prop.type;
  const alternative = (prop.anyOf ?? []).find((entry) => entry.type && entry.type !== "null");
  return alternative?.type ?? "string";
}

/** A nullable field states its shape inside `anyOf`, so every facet is read the way the type is:
 *  off the property, else off the one alternative that is not `null`. */
function specFormat(prop: SchemaProperty): string | undefined {
  return prop.format ?? (prop.anyOf ?? []).find((entry) => entry.format)?.format;
}

function specMaxLength(prop: SchemaProperty): number | undefined {
  return prop.maxLength ?? (prop.anyOf ?? []).find((entry) => entry.maxLength)?.maxLength;
}

/** Whether a field holds a number on the wire, whichever way the schema spells it. */
export function numeric(prop: SchemaProperty): boolean {
  const type = specType(prop);
  return type === "integer" || type === "number";
}

export function initialSpecValue(prop: SchemaProperty, value: unknown): SpecValue {
  if (specType(prop) === "boolean" && !prop.enum) return value === true;
  return value === undefined || value === null ? "" : String(value);
}

/** `2026-08-07T09:00` — the wall-clock string `datetime-local` reads and writes, in the member's
 *  own zone and carrying none. The wire carries an instant, so the two are converted across rather
 *  than sliced: a value typed at 9am in Lisbon submits as `08:00Z` and reads back as 9am. */
const LOCAL_MOMENT_LENGTH = 16;
const MILLISECONDS_PER_MINUTE = 60_000;

function localMoment(wire: string): string {
  const moment = new Date(wire);
  if (Number.isNaN(moment.getTime())) return "";
  const offset = moment.getTimezoneOffset() * MILLISECONDS_PER_MINUTE;
  return new Date(moment.getTime() - offset).toISOString().slice(0, LOCAL_MOMENT_LENGTH);
}

function wireMoment(local: string): string {
  const moment = new Date(local);
  return Number.isNaN(moment.getTime()) ? "" : moment.toISOString();
}

/** A string the schema declines to bound is prose, and prose gets the box that shows more than one
 *  line of it. A field the schema bounds (a cron line, a listing line) is a value, and a value that
 *  fits on a line is typed on one — a format (an email, an instant) names a value the same way a
 *  bound does. */
function prose(prop: SchemaProperty): boolean {
  return (
    specType(prop) === "string" &&
    specMaxLength(prop) === undefined &&
    specFormat(prop) === undefined
  );
}

type SpecFieldProps = {
  name: string;
  prop: SchemaProperty;
  value: SpecValue;
  options?: string[] | null;
  required: boolean;
  layout: FormLayout;
  onChange: (value: SpecValue) => void;
};

/** A spec field read as a row takes the register a stated fact takes: same pitch, same label, same
 *  edge down the right. A form of them under a column of facts is one surface the member reads
 *  down, not two that happen to be stacked. */
const ROW = cn(
  "flex h-(--size-record) items-center justify-between gap-2xl",
  "border-b border-edge text-label",
);

/** How a stored choice is read. The value on the wire stays what the schema named; what the member
 *  reads is that name as words, led with a capital. */
function choiceLabel(choice: string): string {
  return choice.replaceAll("-", " ").replaceAll("_", " ").replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

/** One schema field as the control its shape asks for. A required field carries the requirement so
 *  the browser's own validity applies to it — except a boolean, which holds a value either way: a
 *  checkbox reads `required` as "must be ticked", which would refuse every spec that means false. */
function SpecField({ name, prop, value, options, required, layout, onChange }: SpecFieldProps) {
  const id = "spec-" + name;
  const choices = options ?? prop.enum;
  const label = prop.title ?? name;
  const placeholder = prop.examples?.[0];
  if (!choices && specType(prop) === "boolean")
    return layout === "rows" ? (
      <div className={ROW}>
        <Label htmlFor={id} className="truncate font-normal text-ink-soft">
          {label}
        </Label>
        <Switch
          id={id}
          checked={value === true}
          onChange={(event) => onChange(event.target.checked)}
        />
      </div>
    ) : (
      <div className="flex items-center gap-sm">
        <Switch
          id={id}
          checked={value === true}
          onChange={(event) => onChange(event.target.checked)}
        />
        <Label htmlFor={id}>{label}</Label>
      </div>
    );
  if (layout === "rows" && choices)
    return (
      <div className={ROW}>
        <Label htmlFor={id} className="truncate font-normal text-ink-soft">
          {label}
        </Label>
        <Select required={required} value={String(value)} onValueChange={onChange}>
          <SelectTrigger id={id} className={PLAIN_CONTROL}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {choices.map((choice) => (
              <SelectItem key={choice} value={choice}>
                {choiceLabel(choice)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  return (
    <Field label={label} htmlFor={id}>
      {choices ? (
        <Select required={required} value={String(value)} onValueChange={onChange}>
          <SelectTrigger id={id}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {choices.map((choice) => (
              <SelectItem key={choice} value={choice}>
                {choiceLabel(choice)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : specFormat(prop) === "date-time" ? (
        <Input
          id={id}
          type="datetime-local"
          required={required}
          value={localMoment(String(value))}
          onChange={(event) => onChange(wireMoment(event.target.value))}
        />
      ) : numeric(prop) ? (
        <Input
          id={id}
          type="number"
          required={required}
          placeholder={placeholder}
          value={String(value)}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : prose(prop) ? (
        <Textarea
          id={id}
          required={required}
          placeholder={placeholder}
          value={String(value)}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <Input
          id={id}
          type={specFormat(prop) === "email" ? "email" : "text"}
          required={required}
          placeholder={placeholder}
          maxLength={specMaxLength(prop)}
          value={String(value)}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Field>
  );
}

/** How a schema's fields are laid out. `stacked` is the form a dialog commits — the label over a
 *  control the member types into. `rows` is the settings panel: one ruled row a field, its name on
 *  the left and the control against the right edge, so a screen states what it is set to before it
 *  states how to change it, and its settings read down the same column as the facts above them. */
export type FormLayout = "stacked" | "rows";

export function FormFromSchema({
  schema,
  fields,
  values,
  options,
  layout = "stacked",
  onChange,
}: {
  schema: SpecSchema;
  fields?: string[];
  values: Record<string, SpecValue>;
  options?: Record<string, string[] | null>;
  layout?: FormLayout;
  onChange: (name: string, value: SpecValue) => void;
}) {
  const properties = schema.properties ?? {};
  const shown = fields ?? Object.keys(properties);
  const required = schema.required ?? [];
  return (
    <>
      {shown.map((name) => (
        <SpecField
          key={name}
          name={name}
          prop={properties[name]}
          value={values[name] ?? ""}
          options={options?.[name] ?? null}
          required={required.includes(name)}
          layout={layout}
          onChange={(value) => onChange(name, value)}
        />
      ))}
    </>
  );
}
