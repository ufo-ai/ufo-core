import { Handoff } from "@/components/ui/handoff";
import { CredentialPromptForm } from "@/views/CredentialPrompt";

export function HandoffCredential() {
  return (
    <Handoff>
      <div>The deploy log is behind an API key, and the workspace holds none for Vercel.</div>
      <CredentialPromptForm
        sealed="sealed-2f41"
        prompt={{ slot: "vercel_token", prompt: "Paste a token with read access to the project." }}
        onStored={() => {}}
      />
    </Handoff>
  );
}
