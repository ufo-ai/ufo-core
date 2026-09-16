import { SILENT, Toast } from "@/components/ui/toast";

export function ToastSilent() {
  return (
    <div className="relative h-(--size-tile) w-full">
      <Toast state={SILENT} onDone={() => {}} position="surface" />
    </div>
  );
}
