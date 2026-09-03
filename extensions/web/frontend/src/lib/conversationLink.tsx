import { chatHash } from "@/lib/route";

/** The conversation a record belongs to, as the address the portal's router reads. The whole link
 *  is that address, so inside a framed app page the shell takes the press over the bridge — which a
 *  press answered in the page would stop, moving the frame's own address and nothing the member
 *  sees. The value is a uuid, which is the wire's word for a thread: it is where the member goes,
 *  never a fact they read, so the id carries the press and nothing else states it. */
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
