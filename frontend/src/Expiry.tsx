import { useEffect, useState } from "react";
import { api, fmt, type Expiring } from "./api";
import { Empty, StatusPill } from "./ui";

const th = "px-3 py-1.5 text-left text-[10px] font-semibold uppercase tracking-[0.06em] text-zinc-600";
const td = "px-3 py-1.5 text-[11px]";

export default function Expiry({
  refreshKey,
  onOpen,
}: {
  refreshKey: number;
  onOpen: (tradeId: string) => void;
}) {
  const [rows, setRows] = useState<Expiring[]>([]);

  useEffect(() => {
    api.expiring().then(setRows).catch(() => {});
  }, [refreshKey]);

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <header className="border-b border-desk-800 px-3 py-2">
        <h2 className="text-[11px] font-semibold text-zinc-200">RED II Expiry Watch</h2>
        <p className="text-[10px] text-zinc-600">
          Undelivered Guarantees of Origin whose statutory validity closes within 90 days.
          A certificate expires 12 months after the end of its production period
          (Directive (EU) 2018/2001, Article 19) and cannot be cancelled after that —
          a delivery fail here becomes a buy-in at the market's price, not yours.
        </p>
      </header>

      {rows.length === 0 ? (
        <Empty
          title="Nothing expiring inside 90 days."
          hint="Every undelivered certificate position has room in its validity window."
        />
      ) : (
        <table className="w-full">
          <thead className="border-b border-desk-800">
            <tr>
              <th className={th}>Reference</th>
              <th className={th}>Counterparty</th>
              <th className={th}>Technology</th>
              <th className={`${th} text-right`}>Volume MWh</th>
              <th className={th}>Expires</th>
              <th className={`${th} text-right`}>Remaining</th>
              <th className={th}>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const tone =
                r.days_remaining < 15
                  ? "text-rose-400"
                  : r.days_remaining < 45
                    ? "text-amber-400"
                    : "text-zinc-400";
              return (
                <tr
                  key={r.trade_reference}
                  onClick={() => onOpen(r.id)}
                  className="cursor-default border-b border-desk-900 hover:bg-desk-900"
                >
                  <td className={`${td} num text-zinc-300`}>{r.trade_reference}</td>
                  <td className={`${td} text-zinc-400`}>{r.counterparty_name}</td>
                  <td className={`${td} text-zinc-500`}>{fmt.label(r.technology)}</td>
                  <td className={`${td} num text-right text-zinc-300`}>{fmt.num(r.volume)}</td>
                  <td className={`${td} num text-zinc-400`}>{r.expiry}</td>
                  <td className={`${td} num text-right font-medium ${tone}`}>{r.days_remaining}d</td>
                  <td className={td}>
                    <StatusPill status={r.lifecycle_status} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
