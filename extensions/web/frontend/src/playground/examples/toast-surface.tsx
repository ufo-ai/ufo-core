import { Toast } from "@/components/ui/toast";

export function ToastSurface() {
  return (
    <div className="relative h-(--size-tile) w-full">
      <Toast state={{ title: "Copied" }} onDone={() => {}} position="surface" />
    </div>
  );
}
