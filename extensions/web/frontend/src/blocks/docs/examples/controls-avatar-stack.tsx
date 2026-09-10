import { Avatar, AvatarStack } from "@/blocks/avatar";

const PEOPLE = ["Ana Ruiz", "Sam Patel", "Priya Nair", "Ravi Shah"];

export function ControlsAvatarStack() {
  return (
    <AvatarStack>
      {PEOPLE.map((person) => (
        <Avatar key={person} alt={person} fallback={person[0]} size={24} />
      ))}
    </AvatarStack>
  );
}
