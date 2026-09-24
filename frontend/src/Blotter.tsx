import { useEffect, useMemo, useRef, useState } from "react";
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { fmt, type Trade } from "./api";
import { Empty, Kbd, StatusPill, useHotkeys } from "./ui";

/** Certificate detail is the whole point of this desk — it belongs on the row,
 *  not behind a click. This is the string a trader scans to spot a misbooking. */
function specOf(t: Trade): string {
  if (t.goo)
    return `${fmt.label(t.goo.technology)} · ${t.goo.country_of_origin} · ${t.goo.vintage_start.slice(
      0,
      7,
    )}→${t.goo.vintage_end.slice(0, 7)} · ${t.goo.is_supported ? "SUPPORTED" : "UNSUPPORTED"}`;
  if (t.eua) return `Compliance ${t.eua.compliance_year} · ${t.eua.surrender_phase}`;
  return "Fuel Mix Disclosure · GB";
}

function daysToExpiry(t: Trade): number | null {
  if (!t.goo) return null;
  return Math.round((+new Date(t.goo.expiry) - Date.now()) / 86_400_000);
}

export default function Blotter({
  trades,
  selectedId,
  flashing,
  onSelect,
}: {
  trades: Trade[];
  selectedId: string | null;
  flashing: (id: string) => boolean;
  onSelect: (t: Trade | null) => void;
}) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "trade_timestamp", desc: true }]);
  const [filter, setFilter] = useState("");
  const filterRef = useRef<HTMLInputElement>(null);
  const parentRef = useRef<HTMLDivElement>(null);

  const columns = useMemo<ColumnDef<Trade>[]>(
    () => [
      {
        accessorKey: "trade_timestamp",
        header: "Time",
        size: 70,
        cell: (c) => <span className="num text-zinc-600">{fmt.time(c.getValue<string>())}</span>,
      },
      {
        accessorKey: "trade_reference",
        header: "Reference",
        size: 158,
        cell: (c) => <span className="num text-zinc-300">{c.getValue<string>()}</span>,
      },
      {
        accessorKey: "side",
        header: "B/S",
        size: 44,
        cell: (c) => (
          <span
            className={`font-semibold ${
              c.getValue<string>() === "BUY" ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {c.getValue<string>() === "BUY" ? "BUY" : "SELL"}
          </span>
        ),
      },
      {
        accessorKey: "commodity_type",
        header: "Product",
        size: 62,
        cell: (c) => <span className="font-medium text-zinc-300">{c.getValue<string>()}</span>,
      },
      { accessorKey: "counterparty_name", header: "Counterparty", size: 200 },
      {
        id: "spec",
        header: "Certificate Specification",
        size: 320,
        accessorFn: specOf,
        cell: (c) => <span className="truncate text-zinc-500">{c.getValue<string>()}</span>,
      },
      {
        accessorKey: "volume",
        header: "Volume",
        size: 96,
        cell: (c) => <span className="num block text-right">{fmt.num(c.getValue<string>())}</span>,
      },
      {
        accessorKey: "unit",
        header: "Unit",
        size: 48,
        cell: (c) => <span className="text-[10px] text-zinc-600">{c.getValue<string>()}</span>,
      },
      {
        accessorKey: "price",
        header: "Price",
        size: 78,
        cell: (c) => <span className="num block text-right">{fmt.num(c.getValue<string>(), 4)}</span>,
      },
      {
        accessorKey: "notional",
        header: "Notional",
        size: 128,
        cell: (c) => (
          <span className="num block text-right text-zinc-100">
            {fmt.num(c.getValue<string>(), 2)}
            <span className="ml-1 text-[10px] text-zinc-600">{c.row.original.currency}</span>
          </span>
        ),
      },
      {
        id: "expiry",
        header: "RED II",
        size: 68,
        accessorFn: (t) => daysToExpiry(t) ?? 99999,
        cell: (c) => {
          const d = daysToExpiry(c.row.original);
          if (d === null) return <span className="block text-right text-zinc-800">—</span>;
          const tone = d < 30 ? "text-rose-400" : d < 90 ? "text-amber-400" : "text-zinc-600";
          return <span className={`num block text-right ${tone}`}>{d}d</span>;
        },
      },
      {
        accessorKey: "settlement_date",
        header: "Settles",
        size: 88,
        cell: (c) => <span className="num text-zinc-500">{c.getValue<string>()}</span>,
      },
      {
        accessorKey: "lifecycle_status",
        header: "Status",
        size: 178,
        cell: (c) => <StatusPill status={c.getValue<string>()} />,
      },
      {
        accessorKey: "trader_id",
        header: "Trader",
        size: 84,
        cell: (c) => <span className="text-zinc-600">{c.getValue<string>()}</span>,
      },
    ],
    [],
  );

  const table = useReactTable({
    data: trades,
    columns,
    state: { sorting, globalFilter: filter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const rows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 30,
    overscan: 16,
  });

  const move = (delta: number) => {
    if (!rows.length) return;
    const current = rows.findIndex((r) => r.original.id === selectedId);
    const next = Math.min(rows.length - 1, Math.max(0, current === -1 ? 0 : current + delta));
    onSelect(rows[next].original);
    virtualizer.scrollToIndex(next, { align: "auto" });
  };

  useHotkeys({
    "/": () => filterRef.current?.focus(),
    j: () => move(1),
    k: () => move(-1),
    ArrowDown: () => move(1),
    ArrowUp: () => move(-1),
  });

  // Keep the keyboard cursor visible when the filter narrows the list under it.
  useEffect(() => {
    if (selectedId && !rows.some((r) => r.original.id === selectedId)) onSelect(null);
  }, [rows.length]);

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-desk-800 bg-desk-900 px-3 py-1.5">
        <div className="relative">
          <input
            ref={filterRef}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && (setFilter(""), e.currentTarget.blur())}
            placeholder="Filter reference, counterparty, technology, status…"
            className="w-[26rem] rounded border border-desk-700 bg-desk-850 py-1 pl-2.5 pr-8 text-[11px] outline-none placeholder:text-zinc-600 focus:border-zinc-500"
          />
          <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2">
            <Kbd>/</Kbd>
          </span>
        </div>
        <span className="num text-[11px] text-zinc-600">
          {rows.length === trades.length ? `${trades.length} trades` : `${rows.length} of ${trades.length}`}
        </span>
        <span className="ml-auto flex items-center gap-2 text-[10px] text-zinc-700">
          <Kbd>j</Kbd>
          <Kbd>k</Kbd> navigate
          <Kbd>n</Kbd> new ticket
          <Kbd>?</Kbd> keys
        </span>
      </div>

      <div ref={parentRef} className="min-h-0 flex-1 overflow-auto">
        <div className="min-w-max">
          <div className="sticky top-0 z-10 flex border-b border-desk-800 bg-desk-900">
            {table.getFlatHeaders().map((header) => {
              const sorted = header.column.getIsSorted();
              return (
                <button
                  key={header.id}
                  style={{ width: header.getSize() }}
                  onClick={header.column.getToggleSortingHandler()}
                  className={`shrink-0 px-2 py-[7px] text-left text-[10px] font-semibold uppercase tracking-[0.06em] hover:text-zinc-200 ${
                    sorted ? "text-zinc-200" : "text-zinc-600"
                  }`}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                  <span className="text-zinc-600">
                    {{ asc: " ↑", desc: " ↓" }[sorted as string] ?? ""}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
            {virtualizer.getVirtualItems().map((vi) => {
              const row = rows[vi.index];
              const t = row.original;
              const selected = t.id === selectedId;
              return (
                <div
                  key={row.id}
                  onClick={() => onSelect(t)}
                  className={`absolute left-0 flex w-full cursor-default items-center border-b border-desk-900 text-[11px] ${
                    selected
                      ? "bg-desk-800 shadow-[inset_2px_0_0_0_theme(colors.zinc.300)]"
                      : "hover:bg-desk-850"
                  } ${flashing(t.id) ? "flash" : ""}`}
                  style={{ height: vi.size, transform: `translateY(${vi.start}px)` }}
                >
                  {row.getVisibleCells().map((cell) => (
                    <div
                      key={cell.id}
                      style={{ width: cell.column.getSize() }}
                      className="shrink-0 truncate px-2"
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>

          {rows.length === 0 && (
            <Empty
              title={trades.length ? "No trades match this filter." : "No trades booked yet."}
              hint={trades.length ? "Press / to edit the filter, Esc to clear." : "Press n to open a ticket."}
            />
          )}
        </div>
      </div>
    </section>
  );
}
