import { Avatar } from "@/blocks/avatar";

const FACE =
  "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'><rect width='64' height='64' fill='%230095ff'/><circle cx='32' cy='24' r='11' fill='%23faf9f7'/><circle cx='32' cy='60' r='20' fill='%23faf9f7'/></svg>";

export function ControlsAvatar() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
      <Avatar alt="Ana Ruiz" src={FACE} size={16} />
      <Avatar alt="Ana Ruiz" src={FACE} size={24} />
      <Avatar alt="Ana Ruiz" src={FACE} size={32} />
      <Avatar alt="Sam Patel" fallback="SP" size={32} />
      <Avatar alt="Assistant" gradient={["#0095ff", "#ff6700"]} size={32} />
    </div>
  );
}
