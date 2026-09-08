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

/** `null` is how a spec clears a field the kind holds, which an empty string cannot say: a kind reads
 *  that as the empty value rather than as no value at all. */
export type SpecValue = string | boolean | null;

function specType(prop: SchemaProperty): string {
  if (prop.type) return prop.type;
  const alternative = (prop.anyOf ?? []).find((entry) => entry.type && entry.type !== "null");
  return alternative?.type ?? "string";
}

function specFormat(prop: SchemaProperty): string | undefined {
  return prop.format ?? (prop.anyOf ?? []).find((entry) => entry.format)?.format;
}

function specMaxLength(prop: SchemaProperty): number | undefined {
  return prop.maxLength ?? (prop.anyOf ?? []).find((entry) => entry.maxLength)?.maxLength;
}

export function numeric(prop: SchemaProperty): boolean {
  const type = specType(prop);
  return type === "integer" || type === "number";
}

export function initialSpecValue(prop: SchemaProperty, value: unknown): SpecValue {
  if (specType(prop) === "boolean" && !prop.enum) return value === true;
  return value === undefined || value === null ? "" : String(value);
}

const LOCAL_MOMENT_LENGTH = 16;
const MILLISECONDS_PER_MINUTE = 60_000;

export function localMoment(wire: string): string {
  const moment = new Date(wire);
  if (Number.isNaN(moment.getTime())) return "";
  const offset = moment.getTimezoneOffset() * MILLISECONDS_PER_MINUTE;
  return new Date(moment.getTime() - offset).toISOString().slice(0, LOCAL_MOMENT_LENGTH);
}

export function wireMoment(local: string): string {
  const moment = new Date(local);
  return Number.isNaN(moment.getTime()) ? "" : moment.toISOString();
}

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

const ROW = cn(
  "flex h-(--size-record) items-center justify-between gap-2xl",
  "border-b border-edge text-label",
);

function choiceLabel(choice: string): string {
  return choice.replaceAll("-", " ").replaceAll("_", " ").replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

/** A boolean carries no requirement: a checkbox reads `required` as must be ticked, which would refuse
 *  every spec that means false. */
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
