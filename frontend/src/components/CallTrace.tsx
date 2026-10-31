/* 调用轨迹（可观测性 v2）：这一轮**已记录的外部调用**（模型 + 工具）的时间序。
 *
 * 它答的是「它刚才做了什么」——`Round.dropped` 是**结果**留痕（失败/没搜到/预算带不下，
 * 用户可见的"丢了几条"），这里是**调用**的时间序（含成功、耗时、失败分类）。
 * 同一事实的两面，视角不同，**不是重复**。
 *
 * **只读**：历史轮也显示（`06-前端.md` 的历史轮横幅口径）。空数据只说明没有调用记录，
 * 不能据此判断实际调用次数——**不造假数据**。 */

import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { RoundCalls } from "../api/client";
import { durationText } from "../state/admin";

export function CallTrace({ roundId }: { roundId: string }) {
  const [calls, setCalls] = useState<RoundCalls | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    setCalls(null);
    setError(false);
    api
      .getRoundCalls(roundId)
      .then((data) => {
        if (alive) setCalls(data);
      })
      .catch(() => {
        if (alive) setError(true);
      });
    return () => {
      alive = false;
    };
  }, [roundId]);

  const heading =
    calls === null
      ? "调用轨迹"
      : `调用轨迹 · 模型 ${String(calls.model_calls)} 次 / 工具 ${String(calls.tool_calls)} 次`;

  return (
    <div className="loop__group">
      <details className="q__whybox">
        <summary className="muted small">
          <span>{heading}</span>
          {calls !== null && (
            <span title="记录条数，不代表实际总调用次数">（已记录）</span>
          )}
        </summary>
        {error ? (
          <p className="muted small">调用轨迹读取失败。</p>
        ) : calls === null ? (
          <p className="muted small">读取中…</p>
        ) : calls.calls.length === 0 ? (
          <p className="muted small">
            这一轮还没有调用记录，可能未开启留样；不能据此判断实际调用次数。
          </p>
        ) : (
          <table className="call-trace">
            <thead>
              <tr>
                <th scope="col">时刻</th>
                <th scope="col">类型</th>
                <th scope="col">名字</th>
                <th scope="col">耗时</th>
                <th scope="col">成败</th>
                <th scope="col">失败分类</th>
              </tr>
            </thead>
            <tbody>
              {calls.calls.map((entry, index) => (
                <tr key={`${entry.at}-${entry.kind}-${index}`}>
                  <td className="mono small">{entry.at}</td>
                  <td>{entry.kind}</td>
                  <td>{entry.name}</td>
                  <td>{durationText(entry.duration_ms)}</td>
                  <td>{entry.ok ? "成功" : "失败"}</td>
                  <td>{entry.ok ? "—" : entry.error_kind ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>
    </div>
  );
}
