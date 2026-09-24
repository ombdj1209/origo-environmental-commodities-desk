import { useEffect, useMemo, useRef, useState } from "react";
import { api, TECHNOLOGIES, type Counterparty } from "./api";
import { Kbd } from "./ui";

/** Unit and currency are derived from the product, never typed. Booking EUR
 *  against a REGO, or tCO2 against a certificate, is the most expensive
 *  fat-finger on this desk — so the ticket does not offer the mistake. */
const PRODUCT = {
  GOO: { unit: "MWH", currency: "EUR", label: "EECS Guarantee of Origin", venue: "AIB Hub · national registries" },
  REGO: { unit: "MWH", currency: "GBP", label: "UK REGO", venue: "Ofgem Renewables & CHP Register" },
  EUA: { unit: "TCO2", currency: "EUR", label: "EU Allowance, Phase IV", venue: "EU Union Registry" },
} as const;

type Product = keyof typeof PRODUCT;

const ISSUING_BODY: Record<string, string> = {
  NL: "VertiCer", DE: "Umweltbundesamt (HKNR)", FR: "Grexel / EEX", NO: "Statnett",
  SE: "Energimyndigheten", ES: "CNMC", IT: "GSE", DK: "Energinet", FI: "Grexel", BE: "VREG",
};

const field =
  "w-full rounded border border-desk-700 bg-desk-850 px-2 py-1.5 text-[11px] text-zinc-200 outline-none focus:border-zinc-500";
const label = "mb-1 block text-[10px] font-semibold uppercase tracking-[0.06em] text-zinc-600";

function monthBounds(month: string) {
  const [y, m] = month.split("-").map(Number);
  return { start: `${month}-01`, end: new Date(y, m, 0).toISOString().slice(0, 10) };
}

function lastMonth() {
  const d = new Date();
  d.setMonth(d.getMonth() - 1);
  return d.toISOString().slice(0, 7);
}

/** Mirrors the server's RED II rule so the trader sees the expiry before booking,
 *  not after a rejection. The API remains the authority. */
function expiryOf(month: string) {
  const [y, m] = month.split("-").map(Number);
  return new Date(y + 1, m, 0).toISOString().slice(0, 10);
}

export default function Ticket({
  counterparties,
  onClose,
  onBooked,
}: {
  counterparties: Counterparty[];
  onClose: () => void;
  onBooked: (reference: string) => void;
}) {
  const [product, setProduct] = useState<Product>("GOO");
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [counterpartyId, setCounterpartyId] = useState("");
  const [trader, setTrader] = useState(localStorage.getItem("trader") ?? "");
  const [price, setPrice] = useState("");
  const [volume, setVolume] = useState("");
  const [settlement, setSettlement] = useState(
    new Date(Date.now() + 2 * 86_400_000).toISOString().slice(0, 10),
  );
  const [month, setMonth] = useState(lastMonth());
  const [technology, setTechnology] = useState<string>("SOLAR_PV");
  const [country, setCountry] = useState("NL");
  const [supported, setSupported] = useState(false);
  const [commissioning, setCommissioning] = useState("");
  const [complianceYear, setComplianceYear] = useState(String(new Date().getFullYear()));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const first = useRef<HTMLSelectElement>(null);

  useEffect(() => first.current?.focus(), []);

  const spec = PRODUCT[product];
  const cpty = counterparties.find((c) => c.id === counterpartyId);
  const notional = useMemo(() => {
    const n = Number(price) * Number(volume);
    return Number.isFinite(n) && n > 0
      ? n.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : "—";
  }, [price, volume]);

  const expiry = product === "GOO" ? expiryOf(month) : null;
  const expiryDays = expiry ? Math.round((+new Date(expiry) - Date.now()) / 86_400_000) : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const { start, end } = monthBounds(month);
    try {
      const trade = await api.createTrade({
        commodity_type: product,
        counterparty_id: counterpartyId,
        trader_id: trader,
        side,
        price,
        volume,
        unit: spec.unit,
        currency: spec.currency,
        settlement_date: settlement,
        goo_attributes:
          product === "GOO"
            ? {
                vintage_start: start,
                vintage_end: end,
                technology,
                country_of_origin: country,
                eecs_domain: `${country}-EECS`,
                is_supported: supported,
                commissioning_date: commissioning || null,
                issuing_body: ISSUING_BODY[country] ?? `${country} Issuing Body`,
              }
            : null,
        eua_attributes:
          product === "EUA"
            ? {
                compliance_year: Number(complianceYear),
                surrender_phase: "Phase IV",
                registry_account: cpty?.union_registry_account_id ?? "",
              }
            : null,
      });
      localStorage.setItem("trader", trader);
      setPrice("");
      setVolume("");
      onBooked(trade.trade_reference);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="absolute inset-0 z-30 flex" onMouseDown={onClose}>
      <form
        onSubmit={submit}
        onMouseDown={(e) => e.stopPropagation()}
        className="slide-in flex w-[22rem] shrink-0 flex-col gap-3 overflow-y-auto border-r border-desk-800 bg-desk-900 p-3 shadow-2xl"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-zinc-300">
            Trade Capture
          </h2>
          <button type="button" onClick={onClose} className="text-[10px] text-zinc-600 hover:text-zinc-300">
            <Kbd>esc</Kbd>
          </button>
        </div>

        <div className="grid grid-cols-3 gap-1">
          {(Object.keys(PRODUCT) as Product[]).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setProduct(p)}
              className={`rounded border py-1.5 text-[11px] font-medium transition-colors ${
                product === p
                  ? "border-zinc-400 bg-zinc-100 text-zinc-900"
                  : "border-desk-700 text-zinc-500 hover:border-zinc-600 hover:text-zinc-300"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
        <p className="-mt-1.5 text-[10px] leading-4 text-zinc-600">
          {spec.label} · settles <span className="num text-zinc-400">{spec.currency}</span> per{" "}
          <span className="num text-zinc-400">{spec.unit}</span>
          <br />
          {spec.venue}
        </p>

        <div className="grid grid-cols-2 gap-1">
          {(["BUY", "SELL"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSide(s)}
              className={`rounded border py-1.5 text-[11px] font-semibold transition-colors ${
                side === s
                  ? s === "BUY"
                    ? "border-emerald-500 bg-emerald-500/15 text-emerald-300"
                    : "border-rose-500 bg-rose-500/15 text-rose-300"
                  : "border-desk-700 text-zinc-600 hover:text-zinc-300"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        <div>
          <label className={label}>Counterparty</label>
          <select
            ref={first}
            required
            value={counterpartyId}
            onChange={(e) => setCounterpartyId(e.target.value)}
            className={field}
          >
            <option value="">Select…</option>
            {counterparties.map((c) => (
              <option key={c.id} value={c.id}>
                {c.legal_name} ({c.country})
              </option>
            ))}
          </select>
          {cpty && (
            <p className="num mt-1 text-[10px] text-zinc-600">
              LEI {cpty.lei}
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className={label}>Volume · {spec.unit}</label>
            <input
              required
              type="number"
              step={product === "EUA" ? "0.001" : "1"}
              min="0.001"
              value={volume}
              onChange={(e) => setVolume(e.target.value)}
              className={`${field} num`}
              placeholder="15000"
            />
          </div>
          <div>
            <label className={label}>Price · {spec.currency}</label>
            <input
              required
              type="number"
              step="0.0001"
              min="0.0001"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              className={`${field} num`}
              placeholder="4.2500"
            />
          </div>
        </div>

        <div className="flex items-baseline justify-between rounded border border-desk-700 bg-desk-850 px-2.5 py-2">
          <span className="text-[10px] uppercase tracking-[0.06em] text-zinc-600">Notional</span>
          <span className="num text-sm text-zinc-100">
            {notional}
            <span className="ml-1 text-[10px] text-zinc-500">{spec.currency}</span>
          </span>
        </div>

        {product === "GOO" && (
          <>
            <div className="grid grid-cols-2 gap-2">
              <div>
                {/* ponytail: a production month, not a free range — covers monthly EECS
                    strips. Swap for two date inputs when the desk trades quarterly blocks. */}
                <label className={label}>Production month</label>
                <input
                  required
                  type="month"
                  value={month}
                  onChange={(e) => setMonth(e.target.value)}
                  className={`${field} num`}
                />
              </div>
              <div>
                <label className={label}>Country</label>
                <input
                  required
                  pattern="[A-Za-z]{2}"
                  value={country}
                  onChange={(e) => setCountry(e.target.value.toUpperCase())}
                  className={`${field} num`}
                />
              </div>
            </div>

            {expiry && (
              <p
                className={`-mt-1 text-[10px] ${
                  expiryDays! < 30 ? "text-rose-400" : expiryDays! < 90 ? "text-amber-400" : "text-zinc-600"
                }`}
              >
                RED II validity ends <span className="num">{expiry}</span> · {expiryDays}d ·{" "}
                {ISSUING_BODY[country] ?? "unknown issuing body"}
              </p>
            )}

            <div>
              <label className={label}>Technology</label>
              <select value={technology} onChange={(e) => setTechnology(e.target.value)} className={field}>
                {TECHNOLOGIES.map((t) => (
                  <option key={t} value={t}>
                    {t.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className={label}>Commissioning date · additionality</label>
              <input
                type="date"
                value={commissioning}
                onChange={(e) => setCommissioning(e.target.value)}
                className={`${field} num`}
              />
            </div>

            <label className="flex items-center gap-2 text-[11px] text-zinc-400">
              <input
                type="checkbox"
                checked={supported}
                onChange={(e) => setSupported(e.target.checked)}
                className="accent-amber-500"
              />
              Supported production · FiT, CfD, EEG, SDE++
            </label>
            {supported && (
              <p className="-mt-1.5 text-[10px] text-amber-400/80">
                RE100 and GHG Protocol Scope 2 buyers routinely reject subsidised certificates.
              </p>
            )}
          </>
        )}

        {product === "EUA" && (
          <div>
            <label className={label}>Compliance year</label>
            <input
              required
              type="number"
              min="2021"
              max="2030"
              value={complianceYear}
              onChange={(e) => setComplianceYear(e.target.value)}
              className={`${field} num`}
            />
            <p className="num mt-1 text-[10px] text-zinc-600">
              Union Registry {cpty?.union_registry_account_id ?? "— no account on file"}
            </p>
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className={label}>Settlement</label>
            <input
              required
              type="date"
              value={settlement}
              onChange={(e) => setSettlement(e.target.value)}
              className={`${field} num`}
            />
          </div>
          <div>
            <label className={label}>Trader</label>
            <input
              required
              value={trader}
              onChange={(e) => setTrader(e.target.value)}
              className={field}
              placeholder="jdoe"
            />
          </div>
        </div>

        {error && (
          <pre className="whitespace-pre-wrap rounded border border-rose-900/70 bg-rose-950/40 p-2 text-[10px] leading-4 text-rose-300">
            {error}
          </pre>
        )}

        <button
          type="submit"
          disabled={busy}
          className="sticky bottom-0 rounded bg-zinc-100 py-2 text-[11px] font-semibold text-zinc-900 transition-colors hover:bg-white disabled:opacity-40"
        >
          {busy ? "Booking…" : `Book ${side} ${product}`}
        </button>
      </form>
      <div className="flex-1" />
    </div>
  );
}
