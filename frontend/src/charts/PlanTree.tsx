import { useState } from "react";
import type { PlanNode } from "../types";
import { cx } from "../components/ui";
import { keyPattern, num } from "../utils/format";

const TONE: Record<string, string> = {
  COLLSCAN: "border-bad bg-bad/10 text-bad",
  IXSCAN: "border-ok bg-ok/10 text-ok",
  FETCH: "border-info bg-info/10 text-info",
  SORT: "border-warn bg-warn/10 text-warn",
  LIMIT: "border-line bg-raised text-ink",
  OR: "border-warn bg-warn/10 text-warn",
};

function Node({ node, depth }: { node: PlanNode; depth: number }) {
  const [open, setOpen] = useState(depth < 3);
  const tone = TONE[node.stage] ?? "border-line bg-raised text-ink";
  const stats: [string, number | null | undefined][] = [
    ["returned", node.n_returned],
    ["docs examined", node.docs_examined],
    ["keys examined", node.keys_examined],
    ["est. ms", node.exec_time_ms_estimate],
  ];
  return (
    <li className="relative pl-5 before:absolute before:left-1.5 before:top-0 before:h-full before:border-l before:border-line">
      <div className={cx("my-1.5 rounded-md border px-3 py-2", tone)} data-testid={`plan-stage-${node.stage}`}>
        <div className="flex flex-wrap items-center gap-2">
          {node.children.length > 0 && (
            <button aria-label={open ? "Collapse stage" : "Expand stage"} aria-expanded={open} onClick={() => setOpen(!open)} className="text-xs">
              {open ? "▾" : "▸"}
            </button>
          )}
          <span className="font-mono text-sm font-bold">{node.stage}</span>
          {node.raw_stage && <span className="text-xs opacity-70">({node.raw_stage})</span>}
          {node.index_name && <span className="font-mono text-xs">index: {node.index_name}</span>}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs">
          {stats.filter(([, v]) => v !== undefined).map(([k, v]) => (
            <span key={k}><span className="opacity-70">{k}</span> <b className="tabular-nums">{num(v ?? null)}</b></span>
          ))}
          {node.key_pattern && <span className="font-mono">{keyPattern(node.key_pattern)}</span>}
          {node.direction && <span>{node.direction}</span>}
        </div>
        {node.index_bounds && (
          <details className="mt-1 text-xs">
            <summary className="cursor-pointer opacity-80">index bounds</summary>
            <pre className="mt-1 overflow-auto font-mono">{JSON.stringify(node.index_bounds, null, 1)}</pre>
          </details>
        )}
      </div>
      {open && node.children.length > 0 && (
        <ul>{node.children.map((c, i) => <Node key={i} node={c} depth={depth + 1} />)}</ul>
      )}
    </li>
  );
}

/** Root of the tree is the final stage; children are the inputs feeding it. */
export function PlanTree({ tree }: { tree: PlanNode | null }) {
  if (!tree) return <p className="text-sm text-muted">No plan structure available.</p>;
  return (
    <ul data-testid="plan-tree" aria-label="Execution plan stages">
      <Node node={tree} depth={0} />
    </ul>
  );
}
