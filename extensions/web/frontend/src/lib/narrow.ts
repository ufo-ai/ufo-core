import { useEffect, useState } from "react";

export const NARROW = "(width < 720px)";

/** Whether the portal is drawing its phone layout: the hamburger stands on the bar, the drawer it
 *  opens holds the selected section's own list, and a row of lanes pages one lane to a screen. */
export function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW).matches);
  useEffect(() => {
    const query = window.matchMedia(NARROW);
    const answer = () => setNarrow(query.matches);
    query.addEventListener("change", answer);
    return () => query.removeEventListener("change", answer);
  }, []);
  return narrow;
}
