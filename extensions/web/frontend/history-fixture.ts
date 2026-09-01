import type { Plugin } from "vite";

export const HISTORY_FIXTURE_AGENT_ID = "11111111-1111-4111-8111-111111111111";
export const HISTORY_FIXTURE_ROW_COUNT = 30;

const SURFACES = {
  admin: false,
  memory: false,
  "community-skills": false,
  "installed-skills": false,
};

const OBJECTS = Array.from({ length: HISTORY_FIXTURE_ROW_COUNT }, (_, at) => ({
  name: `00000000-0000-4000-8000-${String(at).padStart(12, "0")}`,
  agent_id: HISTORY_FIXTURE_AGENT_ID,
  agent_name: "assistant",
  title: `History row ${String(at + 1).padStart(2, "0")}`,
  last_at: new Date(Date.UTC(2026, 7, 31, 12, 0, -at)).toISOString(),
  surface: "web",
  surface_label: null,
  mine: true,
  speaker: null,
}));

/** The local responses that draw a new chat with enough history to scroll. */
export function historyFixturePayload(pathname: string): unknown | null {
  if (pathname === "/surface/web/api/agents") {
    return {
      agents: [
        {
          id: HISTORY_FIXTURE_AGENT_ID,
          name: "assistant",
          model: "opus",
          main: true,
          icon: "propylon",
          app: "chat",
        },
      ],
      archived: [],
      member: { id: "fixture-member", email: "member@example.com", admin: false },
      surfaces: SURFACES,
    };
  }
  if (pathname === "/surface/web/objects/conversation") {
    return { objects: OBJECTS, next_cursor: null };
  }
  if (pathname === "/surface/web/api/agents/status") return { statuses: [] };
  return null;
}

/** The local boot and history responses in a Vite server. */
export function historyFixture(): Plugin {
  return {
    name: "history-fixture",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.method !== "GET") {
          next();
          return;
        }
        const pathname = new URL(request.url ?? "/", "http://ufo.localhost").pathname;
        const payload = historyFixturePayload(pathname);
        if (payload === null) {
          next();
          return;
        }
        response.statusCode = 200;
        response.setHeader("content-type", "application/json");
        response.end(JSON.stringify(payload));
      });
    },
  };
}
