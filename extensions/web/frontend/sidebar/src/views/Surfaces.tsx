/** The surfaces a member connects, which is one component on both shells: the rows, the Slack
 *  install and its watch live in the lanes shell's copy and are drawn from there, with that
 *  module's `@/…` imports resolved against this shell's own tree. This file is the sidebar half of
 *  that seam, and it goes when the sidebar shell does. */
export {
  CONNECT_INSTALLS,
  Connect,
  ConnectSurfaces,
  SURFACES_READ,
  useConnected,
} from "../../../src/views/Surfaces";
export type { SurfaceRow, SurfacesPayload } from "../../../src/views/Surfaces";
