import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Blotter from "./Blotter";
import Detail from "./Detail";
import Expiry from "./Expiry";
import Positions from "./Positions";
import Ticket from "./Ticket";
import {
  api,
  fmt,
  LIFECYCLE,
  type Counterparty,
  type Expiring,
  type Lifecycle,
  type Trade,
  type Vwap,
} from "./api";
import { Kbd, STATUS_DOT, useHotkeys } from "./ui";

type View = "blotter" | "positions" | "expiry";
const VIEWS: { id: View; label: string; key: string }[] = [
  { id: "blotter", label: "Blotter", key: "1" },
  { id: "positions", label: "Positions", key: "2" },
  { id: "expiry", label: "Expiry watch", key: "3" },
];

function Tile({ label, value, unit, tone }: { label: string; value: string; unit?: string; tone?: string }) {
  return (
    <div className="border-l border-desk-800 px-3 py-0.5">
      <p className="text-[9px] uppercase tracking-[0.08em] text-zinc-600">{label}</p>
      <p className={`num text-[13px] leading-tight ${tone ?? "text-zinc-100"}`}>
        {value}
        {unit && <span className="ml-1 text-[9px] text-zinc-600">{unit}</span>}
      </p>
    </div>
  );
}

export default function App() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [counterparties, setCounterparties] = useState<Counterparty[]>([]);
  const [vwap, setVwap] = useState<Vwap | null>(null);
  const [expiring, setExpiring] = useState<Expiring[]>([]);
  const [selected, setSelected] = useState<Trade | null>(null);
  const [view, setView] = useState<View>("blotter");
  const [statusFilter, setStatusFilter] = useState<Lifecycle | null>(null);
  const [ticketOpen, setTicketOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [live, setLive] = useState(false);
  const [toasts, setToasts] = useState<{ id: number; text: string }[]>([]);
  const [analyticsKey, setAnalyticsKey] = useState(0);
  const [loading, setLoading] = useState(true);

  const selectedId = useRef<string | null>(null);
  selectedId.current = selected?.id ?? null;
  const flashUntil = useRef(new Map<string, number>());
  const [, tick] = useState(0);

  const flash = useCallback((id: string) => {
    flashUntil.current.set(id, Date.now() + 1400);
    setTimeout(() => {
      flashUntil.current.delete(id);
      tick((n) => n + 1);
    }, 1400);
  }, []);

  const upsert = useCallback(
    (t: Trade, announce = false) => {
      setTrades((prev) => {
        const i = prev.findIndex((x) => x.id === t.id);
        if (i === -1) return [t, ...prev];
        const next = [...prev];
        next[i] = t;
        return next;
      });
      flash(t.id);
      if (selectedId.current === t.id) setSelected(t);
      if (announce) {
        const id = Date.now() + Math.random();
        setToasts((s) => [
          ...s,
          { id, text: `${t.trader_id} booked ${t.side} ${fmt.num(t.volume)} ${t.unit} ${t.commodity_type}` },
        ]);
        setTimeout(() => setToasts((s) => s.filter((x) => x.id !== id)), 4500);
      }
    },
    [flash],
  );

  const refreshAnalytics = useCallback(() => {
    setAnalyticsKey((n) => n + 1);
    api.vwap(new Date().getFullYear()).then(setVwap).catch(() => {});
    api.expiring().then(setExpiring).catch(() => {});
  }, []);

  useEffect(() => {
    Promise.all([api.trades().then(setTrades), api.counterparties().then(setCounterparties)])
      .catch(() => {})
      .finally(() => setLoading(false));
    refreshAnalytics();
  }, [refreshAnalytics]);

  // Live blotter stream. Reconnects on drop — a desk display that silently goes
  // stale is more dangerous than one that admits it is disconnected.
  useEffect(() => {
    let ws: WebSocket;
    let retry: number;
    let ping: number;
    const me = () => localStorage.getItem("trader");
    const connect = () => {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/blotter`);
      ws.onopen = () => {
        setLive(true);
        ping = window.setInterval(() => ws.readyState === 1 && ws.send("ping"), 25_000);
      };
      ws.onmessage = (e) => {
        const event = JSON.parse(e.data);
        if (!event.trade) return;
        upsert(event.trade, event.type === "TRADE_CAPTURED" && event.trade.trader_id !== me());
        if (event.type === "TRADE_CAPTURED") refreshAnalytics();
      };
      ws.onclose = () => {
        setLive(false);
        clearInterval(ping);
        retry = window.setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      clearTimeout(retry);
      clearInterval(ping);
      ws.onclose = null;
      ws.close();
    };
  }, [upsert, refreshAnalytics]);

  useHotkeys({
    n: () => setTicketOpen(true),
    "?": () => setHelpOpen((v) => !v),
    "1": () => setView("blotter"),
    "2": () => setView("positions"),
    "3": () => setView("expiry"),
    Escape: () => {
      if (helpOpen) return setHelpOpen(false);
      if (ticketOpen) return setTicketOpen(false);
      setSelected(null);
    },
  });

  const counts = useMemo(() => {
    const c = Object.fromEntries(LIFECYCLE.map((s) => [s, 0])) as Record<Lifecycle, number>;
    for (const t of trades) c[t.lifecycle_status]++;
    return c;
  }, [trades]);

  const visible = statusFilter ? trades.filter((t) => t.lifecycle_status === statusFilter) : trades;
  const open = trades.filter((t) => !["SETTLED", "RETIRED"].includes(t.lifecycle_status));
  const openEur = open
    .filter((t) => t.currency === "EUR")
    .reduce((s, t) => s + Number(t.notional) * (t.side === "BUY" ? 1 : -1), 0);
  const unconfirmed = counts.BOOKED + counts.CONFIRMATION_PENDING;
  const urgent = expiring.filter((e) => e.days_remaining < 30).length;

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-stretch border-b border-desk-800 bg-desk-900">
        <div className="flex flex-col justify-center px-3 py-1.5">
          <h1 className="text-[12px] font-semibold leading-tight text-zinc-100">
            Environmental Commodities Desk
          </h1>
          <p className="text-[9px] leading-tight text-zinc-600">
            EECS GOO · UK REGO · EU ETS — OTC capture, operations &amp; settlement
          </p>
        </div>

        <nav className="flex items-center gap-px border-l border-desk-800 px-2">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              onClick={() => setView(v.id)}
              className={`rounded px-2.5 py-1 text-[11px] transition-colors ${
                view === v.id ? "bg-desk-800 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {v.label}
              <span className="ml-1.5 text-[9px] text-zinc-700">{v.key}</span>
            </button>
          ))}
        </nav>

        <Tile label="Open trades" value={String(open.length)} />
        <Tile
          label="Net EUR exposure"
          value={fmt.num(openEur, 2)}
          unit="EUR"
          tone={openEur >= 0 ? "text-emerald-400" : "text-rose-400"}
        />
        <Tile
          label="Awaiting confirmation"
          value={String(unconfirmed)}
          tone={unconfirmed ? "text-amber-400" : "text-zinc-100"}
        />
        <Tile
          label="Solar − Hydro RoR"
          value={vwap?.solar_to_hydro_spread ? fmt.num(vwap.solar_to_hydro_spread, 4) : "—"}
          unit="EUR/MWh"
        />
        <Tile
          label="Expiring < 30d"
          value={String(urgent)}
          tone={urgent ? "text-rose-400" : "text-zinc-500"}
        />

        <div className="ml-auto flex items-center gap-3 border-l border-desk-800 px-3">
          <button
            onClick={() => setTicketOpen(true)}
            className="rounded bg-zinc-100 px-3 py-1 text-[11px] font-semibold text-zinc-900 hover:bg-white"
          >
            New ticket <span className="ml-1 font-normal text-zinc-500">n</span>
          </button>
          <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.06em]">
            <span
              className={`h-1.5 w-1.5 rounded-full ${live ? "bg-emerald-400" : "animate-pulse bg-rose-500"}`}
            />
            <span className={live ? "text-emerald-400" : "text-rose-400"}>
              {live ? "Live" : "Reconnecting"}
            </span>
          </span>
        </div>
      </header>

      {/* Settlement pipeline — also the blotter's status filter. */}
      <div className="flex shrink-0 items-stretch border-b border-desk-800 bg-desk-950 text-[10px]">
        <button
          onClick={() => setStatusFilter(null)}
          className={`px-3 py-1.5 ${statusFilter === null ? "text-zinc-100" : "text-zinc-600 hover:text-zinc-300"}`}
        >
          All <span className="num ml-1">{trades.length}</span>
        </button>
        {LIFECYCLE.map((s) => (
          <button
            key={s}
            onClick={() => {
              setStatusFilter(statusFilter === s ? null : s);
              setView("blotter");
            }}
            disabled={!counts[s]}
            className={`flex items-center gap-1.5 border-l border-desk-900 px-3 py-1.5 transition-colors disabled:opacity-30 ${
              statusFilter === s ? "bg-desk-800 text-zinc-100" : "text-zinc-500 hover:text-zinc-200"
            }`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[s]}`} />
            {fmt.label(s)}
            <span className="num text-zinc-500">{counts[s]}</span>
          </button>
        ))}
        {statusFilter && (
          <span className="ml-auto self-center px-3 text-zinc-600">
            filtered — <Kbd>esc</Kbd> or click again to clear
          </span>
        )}
      </div>

      <main className="relative flex min-h-0 flex-1">
        {loading ? (
          <div className="flex flex-1 items-center justify-center text-[11px] text-zinc-600">
            Loading desk…
          </div>
        ) : view === "blotter" ? (
          <Blotter
            trades={visible}
            selectedId={selected?.id ?? null}
            flashing={(id) => flashUntil.current.has(id)}
            onSelect={setSelected}
          />
        ) : view === "positions" ? (
          <Positions refreshKey={analyticsKey} />
        ) : (
          <Expiry
            refreshKey={analyticsKey}
            onOpen={(id) => {
              const t = trades.find((x) => x.id === id);
              if (t) {
                setSelected(t);
                setView("blotter");
              }
            }}
          />
        )}

        {selected && view === "blotter" && (
          <Detail
            trade={selected}
            onClose={() => setSelected(null)}
            onChanged={(t) => {
              upsert(t);
              refreshAnalytics();
            }}
          />
        )}

        {ticketOpen && (
          <Ticket
            counterparties={counterparties}
            onClose={() => setTicketOpen(false)}
            onBooked={(reference) => {
              setTicketOpen(false);
              refreshAnalytics();
              if (!live) api.trades().then(setTrades).catch(() => {});
              const id = Date.now();
              setToasts((s) => [...s, { id, text: `Booked ${reference} — confirmation rendering` }]);
              setTimeout(() => setToasts((s) => s.filter((x) => x.id !== id)), 4500);
            }}
          />
        )}
      </main>

      <div className="pointer-events-none absolute bottom-3 right-3 z-40 flex flex-col gap-1.5">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="rise rounded border border-desk-700 bg-desk-850 px-3 py-2 text-[11px] text-zinc-200 shadow-lg"
          >
            {t.text}
          </div>
        ))}
      </div>

      {helpOpen && (
        <div
          className="absolute inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setHelpOpen(false)}
        >
          <div className="rise w-80 rounded border border-desk-700 bg-desk-900 p-4">
            <h2 className="mb-3 text-[11px] font-semibold uppercase tracking-[0.06em] text-zinc-300">
              Keyboard
            </h2>
            <dl className="space-y-1.5 text-[11px] text-zinc-400">
              {[
                ["n", "New trade ticket"],
                ["/", "Focus the blotter filter"],
                ["j / k", "Move down / up the blotter"],
                ["↑ / ↓", "Same, for the arrow-key inclined"],
                ["1 2 3", "Blotter · Positions · Expiry watch"],
                ["esc", "Close panel, clear filter"],
                ["?", "This card"],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between gap-4">
                  <dt>
                    <Kbd>{k}</Kbd>
                  </dt>
                  <dd className="text-right text-zinc-500">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      )}
    </div>
  );
}
