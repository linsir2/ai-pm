/* SSE 订阅：只把事件转交给外面，自己不做判断。
   两个坑必须防住：
   1. onEvent 每次渲染都是新引用 → 用 ref 存，效果只在挂载时跑一次（否则事件流会反复重连）；
   2. 后端没有补发（不支持 Last-Event-ID）→ 断线重连之后必须重读一次工作区。 */

import { useEffect, useRef, useState } from "react";

import type { WorkspaceEvent } from "../api/types";

const EVENT_NAMES = [
  "round.updated",
  "packet.emitted",
  "doc.changed",
  "generation.failed",
  "exploration.updated",
  "discussion.updated",
] as const;

export type ConnectionState = "idle" | "open" | "lost";

export function useEvents(
  onEvent: (event: WorkspaceEvent) => void,
  onReconnect?: () => void,
): { connection: ConnectionState } {
  const handler = useRef(onEvent);
  const afterReconnect = useRef(onReconnect);
  const [connection, setConnection] = useState<ConnectionState>("idle");

  useEffect(() => {
    handler.current = onEvent;
    afterReconnect.current = onReconnect;
  });

  useEffect(() => {
    let source: EventSource | null = null;
    let timer: number | undefined;
    let attempts = 0;
    let wasLost = false;
    let lostAt = 0;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      source = new EventSource("/api/events");

      source.onopen = () => {
        attempts = 0;
        setConnection("open");
        // 只补"真的断过一会儿"的重读：连接抖一下就整份重读，会把用户正在打的字顶掉。
        if (wasLost && Date.now() - lostAt > 3_000) {
          wasLost = false;
          afterReconnect.current?.();
        }
        wasLost = false;
      };

      source.onerror = () => {
        wasLost = true;
        lostAt = Date.now();
        setConnection("lost");
        // EventSource 自己会重连；只有它放弃了（CLOSED）才由我们退避重来。
        if (source?.readyState === EventSource.CLOSED && !stopped) {
          attempts += 1;
          const delay = Math.min(800 * 2 ** attempts, 12_000);
          timer = window.setTimeout(connect, delay);
        }
      };

      for (const name of EVENT_NAMES) {
        source.addEventListener(name, (raw) => {
          try {
            handler.current(JSON.parse((raw as MessageEvent<string>).data) as WorkspaceEvent);
          } catch {
            // 一条坏报文不该把订阅打断；事件只是通知，丢了还有重读兜底。
          }
        });
      }
    };

    connect();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
      source?.close();
    };
  }, []);

  return { connection };
}
