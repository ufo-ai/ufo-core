
import {
  AppConversations,
  Header,
  ObjectPane,
  SectionApp,
  mountApp,
  usePageHead,
} from "ufo/kit";
import type { Placement } from "ufo/kit";

const APP = "Notification";

const PURPOSE =
  "Decides which of the things your agents noticed are worth interrupting you for.";

const KIND = "notification";
const CONVERSATIONS = "Conversations";
const NO_CONVERSATIONS = "Your chats with this app land here.";

function Home({
  agentId,
  place,
  onPlace,
}: {
  agentId: string;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const band = usePageHead(<Header pinned heading={1} title={APP} lede={PURPOSE} />);
  return (
    <>
      {band}
      <ObjectPane
        agentId={agentId}
        kind={KIND}
        makes={false}
        opens={place.opens ?? []}
        onPlace={onPlace}
      />
      <AppConversations
        agentId={agentId}
        title={CONVERSATIONS}
        blank={NO_CONVERSATIONS}
        place={place}
        onPlace={onPlace}
      />
    </>
  );
}

mountApp(document.getElementById("root")!, (init) => (
  <SectionApp
    tab="notification"
    init={init}
    view={{
      label: APP,
      remountOnPlace: false,
      ownsHeader: true,
      render: (place, onPlace) => (
        <Home agentId={init.agentId} place={place} onPlace={onPlace} />
      ),
    }}
  />
));
