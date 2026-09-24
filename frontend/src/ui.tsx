import { useEffect } from "react";
import { fmt } from "./api";

export const STATUS_STYLE: Record<string, string> = {
  BOOKED: "bg-sky-500/10 text-sky-300 ring-sky-500/25",
  CONFIRMATION_PENDING: "bg-amber-500/10 text-amber-300 ring-amber-500/25",
  CONFIRMED: "bg-violet-500/10 text-violet-300 ring-violet-500/25",
  REGISTRY_TRANSFER_INITIATED: "bg-cyan-500/10 text-cyan-300 ring-cyan-500/25",
  DELIVERED: "bg-teal-500/10 text-teal-300 ring-teal-500/25",
  SETTLED: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/25",
  RETIRED: "bg-zinc-500/10 text-zinc-400 ring-zinc-500/25",
};

export const STATUS_DOT: Record<string, string> = {
  BOOKED: "bg-sky-400",
  CONFIRMATION_PENDING: "bg-amber-400",
  CONFIRMED: "bg-violet-400",
  REGISTRY_TRANSFER_INITIATED: "bg-cyan-400",
  DELIVERED: "bg-teal-400",
  SETTLED: "bg-emerald-400",
  RETIRED: "bg-zinc-500",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={`inline-block truncate rounded-sm px-1.5 py-[3px] text-[10px] font-medium tracking-wide ring-1 ring-inset ${
        STATUS_STYLE[status] ?? ""
      }`}
    >
      {fmt.label(status)}
    </span>
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-zinc-700 bg-zinc-800 px-1 py-px font-mono text-[10px] text-zinc-400">
      {children}
    </kbd>
  );
}

/** Re-registered every render so handlers never close over stale state. */
export function useHotkeys(map: Record<string, (e: KeyboardEvent) => void>) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = !!el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
      if (typing && e.key !== "Escape") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const fn = map[e.key];
      if (fn) {
        e.preventDefault();
        fn(e);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
}

export function Bar({ value, max, tone = "bg-zinc-500" }: { value: number; max: number; tone?: string }) {
  const pct = max > 0 ? Math.max(1, Math.round((Math.abs(value) / max) * 100)) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-sm bg-zinc-900">
      <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-1 py-16 text-center">
      <p className="text-xs text-zinc-500">{title}</p>
      {hint && <p className="text-[11px] text-zinc-700">{hint}</p>}
    </div>
  );
}
