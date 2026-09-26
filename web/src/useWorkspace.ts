import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { WorkspaceInfo } from './api';

export function useWorkspace() {
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    try {
      const result = await api<WorkspaceInfo>('/workspace', undefined, controller.signal);
      if (!controller.signal.aborted) {
        setWorkspace(result);
        setUnavailable(false);
      }
    } catch {
      if (!controller.signal.aborted) {
        setWorkspace(null);
        setUnavailable(true);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 30_000);
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    return () => {
      pending.current?.abort();
      clearInterval(timer);
      window.removeEventListener('focus', onFocus);
    };
  }, [refresh]);

  return { workspace, unavailable, refresh };
}
