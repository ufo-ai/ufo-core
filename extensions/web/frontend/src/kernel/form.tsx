import { Checkbox, Input, Label, Select } from "@/components/ui/field";
import type { SchemaProperty } from "@/lib/types";

export type SpecSchema = {
  properties?: Record<string, SchemaProperty>;
  required?: string[];
};

export type SpecValue = string | boolean;

function specType(prop: SchemaProperty): string {
  if (prop.type) return prop.type;
  const alternative = (prop.anyOf ?? []).find((entry) => entry.type && entry.type !== "null");
  return alternative?.type ?? "string";
}

export function initialSpecValue(prop: SchemaProperty, value: unknown): SpecValue {
  if (specType(prop) === "boolean" && !prop.enum) return value === true;
  return value === undefined || value === null ? "" : String(value);
}

type SpecFieldProps = {
  name: string;
  prop: SchemaProperty;
  value: SpecValue;
  options?: string[] | null;
  onChange: (value: SpecValue) => void;
};

function SpecField({ name, prop, value, options, onChange }: SpecFieldProps) {
  const id = "spec-" + name;
  const choices = options ?? prop.enum;
  return (
    <div className="mb-lg">
      <Label htmlFor={id}>{name}</Label>
      {choices ? (
        <Select id={id} value={String(value)} onChange={(event) => onChange(event.target.value)}>
          {choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </Select>
      ) : specType(prop) === "boolean" ? (
        <Checkbox
          id={id}
          checked={value === true}
          onChange={(event) => onChange(event.target.checked)}
        />
      ) : (
        <Input id={id} value={String(value)} onChange={(event) => onChange(event.target.value)} />
      )}
    </div>
  );
}

export function FormFromSchema({
  schema,
  fields,
  values,
  options,
  onChange,
}: {
  schema: SpecSchema;
  fields?: string[];
  values: Record<string, SpecValue>;
  options?: Record<string, string[] | null>;
  onChange: (name: string, value: SpecValue) => void;
}) {
  const properties = schema.properties ?? {};
  const shown = fields ?? Object.keys(properties);
  return (
    <>
      {shown.map((name) => (
        <SpecField
          key={name}
          name={name}
          prop={properties[name]}
          value={values[name] ?? ""}
          options={options?.[name] ?? null}
          onChange={(value) => onChange(name, value)}
        />
      ))}
    </>
  );
}
