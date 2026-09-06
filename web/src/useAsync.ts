import { useCallback, useEffect, useState } from "react";

/** Load something, and say which of the three states it is in.
 *
 *  Explicit `error` rather than a silent empty render: this page's whole job
 *  is telling an operator what happened, and a screen that shows nothing when
 *  a request fails is telling them the system is idle. */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const run = useCallback(() => {
    let live = true;
    setLoading(true);
    load()
      .then((got) => live && (setValue(got), setError(null)))
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(run, [run]);
  return { value, error, loading, reload: run };
}
