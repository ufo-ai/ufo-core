import AssistantLane from "@/blocks/docs/examples/compose-assistant";
import CodingLane from "@/blocks/docs/examples/compose-coding";
import MeetingsLane from "@/blocks/docs/examples/compose-meetings";
import TodosLane from "@/blocks/docs/examples/compose-todos";
import "@/blocks/docs/compositions.css";

export default function Shell() {
  return (
    <div className="blk-lanes">
      <AssistantLane />
      <TodosLane />
      <MeetingsLane />
      <CodingLane />
    </div>
  );
}
