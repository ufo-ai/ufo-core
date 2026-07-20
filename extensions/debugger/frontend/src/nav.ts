import { useEffect, useState } from "react";

export type Params = { ws: string | null; c: string | null; t: string | null };

function read(): Params {
  const params = new URLSearchParams(window.location.search);
  return { ws: params.get("ws"), c: params.get("c"), t: params.get("t") };
}

export function useParams(): [Params, (next: Partial<Params>) => void] {
  const [params, setParams] = useState<Params>(read());
  useEffect(() => {
    const onPop = () => setParams(read());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = (next: Partial<Params>) => {
    const merged = { ...read(), ...next };
    const query = new URLSearchParams();
    if (merged.ws) query.set("ws", merged.ws);
    if (merged.c) query.set("c", merged.c);
    if (merged.t) query.set("t", merged.t);
    const search = query.toString();
    window.history.pushState(null, "", search ? `?${search}` : window.location.pathname);
    setParams(read());
  };
  return [params, navigate];
}
