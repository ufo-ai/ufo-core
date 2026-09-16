import { Toast } from "@/components/ui/toast";

export function ToastDescription() {
  return (
    <div className="relative h-(--size-tile) w-full">
      <Toast
        state={{
          title: "Draft saved",
          description: "It is on the release conversation until you send it.",
        }}
        onDone={() => {}}
        position="surface"
      />
    </div>
  );
}
