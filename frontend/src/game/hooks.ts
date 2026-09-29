import { useEffect, useRef, useState } from "react";

/** Force a re-render every `ms` so countdowns stay live. */
export function useTicker(ms = 250): number {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
  return Date.now();
}

/** Auto-scroll a chat log to the bottom when content changes. */
export function useAutoScroll<T extends HTMLElement>(dep: unknown) {
  const ref = useRef<T | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [dep]);
  return ref;
}
