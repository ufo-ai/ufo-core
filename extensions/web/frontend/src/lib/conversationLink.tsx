import { chatHash } from "@/lib/route";

export function ConversationLink({ id }: { id: string }) {
  return (
    <a
      href={chatHash(id)}
      className="text-inherit underline-offset-2 hover:underline focus-visible:underline"
    >
      {id}
    </a>
  );
}
