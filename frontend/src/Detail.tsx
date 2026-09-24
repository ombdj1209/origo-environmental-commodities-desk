import { useEffect, useState } from "react";
import { api, fmt, LIFECYCLE, type AuditEntry, type Lifecycle, type Trade } from "./api";
import { Kbd, STATUS_DOT, StatusPill } from "./ui";

// ponytail: mirrors the backend TRANSITIONS map to decide which button to draw.
// The API re-checks every transition, so drift here is cosmetic, never a hole.
const NEXT: Record<Lifecycle, { to: Lifecycle; verb: string; evidence?: keyof Evidence }[]> = {
  BOOKED: [],
  CONFIRMATION_PENDING: [{ to: "CONFIRMED", verb: "Counterparty acknowledged" }],
  CONFIRMED: [
    { to: "REGISTRY_TRANSFER_INITIATED", verb: "Submit registry transfer", evidence: "registry_transfer_ref" },
  ],
  REGISTRY_TRANSFER_INITIATED: [{ to: "DELIVERED", verb: "Confirm delivery" }],
  DELIVERED: [
    { to: "SETTLED", verb: "Reconcile payment", evidence: "settlement_reference" },
    { to: "RETIRED", verb: "Cancel for beneficiary", evidence: "retirement_beneficiary" },
  ],
  SETTLED: [{ to: "RETIRED", verb: "Cancel for beneficiary", evidence: "retirement_beneficiary" }],
  RETIRED: [],
};

type Evidence = {
  registry_transfer_ref: string;
  settlement_reference: string;
  retirement_beneficiary: string;
};

const EVIDENCE_LABEL: Record<keyof Evidence, string> = {
  registry_transfer_ref: "Registry transfer reference",
  settlement_reference: "Payment / wire reference",
  retirement_beneficiary: "Cancellation beneficiary",
};

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-desk-900 py-[5px]">
      <span className="shrink-0 text-[10px] uppercase tracking-[0.04em] text-zinc-600">{k}</span>
      <span className="num truncate text-right text-[11px] text-zinc-200">{v ?? "—"}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="px-3 pb-2">
      <h3 className="mb-1 mt-2 text-[10px] font-semibold uppercase tracking-[0.06em] text-zinc-500">
        {title}
      </h3>
      {children}
    </div>
  );
}

/** The settlement pipeline as a rail, so an operator can see at a glance how far
 *  a trade has travelled and what is left before cash moves. */
function Rail({ status }: { status: Lifecycle }) {
  const at = LIFECYCLE.indexOf(status);
  return (
    <ol className="flex flex-col gap-0 px-3 py-2">
      {LIFECYCLE.map((s, i) => {
        const done = i < at;
        const here = i === at;
        const retiredBranch = s === "RETIRED" && status !== "RETIRED";
        return (
          <li key={s} className="flex items-center gap-2">
            <div className="flex flex-col items-center">
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                  here ? STATUS_DOT[s] : done ? "bg-zinc-600" : "bg-desk-700"
                }`}
              />
              {i < LIFECYCLE.length - 1 && (
                <span className={`h-3 w-px ${done ? "bg-zinc-700" : "bg-desk-800"}`} />
              )}
            </div>
            <span
              className={`-mt-3 text-[10px] ${
                here ? "font-medium text-zinc-100" : done ? "text-zinc-500" : "text-zinc-700"
              } ${retiredBranch && !done ? "italic" : ""}`}
            >
              {fmt.label(s)}
              {retiredBranch && !done && !here && " (optional)"}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export default function Detail({
  trade,
  onClose,
  onChanged,
}: {
  trade: Trade;
  onClose: () => void;
  onChanged: (t: Trade) => void;
}) {
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [evidence, setEvidence] = useState<Partial<Evidence>>({});
  const [operator, setOperator] = useState(localStorage.getItem("trader") || "ops");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setError("");
    setEvidence({});
    api.audit(trade.id).then(setAudit).catch(() => setAudit([]));
  }, [trade.id, trade.lifecycle_status]);

  async function advance(to: Lifecycle) {
    setBusy(true);
    setError("");
    try {
      onChanged(await api.transition(trade.id, { target_state: to, performed_by: operator, ...evidence }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const actions = NEXT[trade.lifecycle_status];

  return (
    <aside className="flex w-[23rem] shrink-0 flex-col overflow-y-auto border-l border-desk-800 bg-desk-900">
      <header className="sticky top-0 z-10 flex items-start justify-between gap-2 border-b border-desk-800 bg-desk-900 px-3 py-2">
        <div className="min-w-0">
          <p className="num truncate text-[13px] text-zinc-100">{trade.trade_reference}</p>
          <div className="mt-1">
            <StatusPill status={trade.lifecycle_status} />
          </div>
        </div>
        <button onClick={onClose} className="shrink-0 text-zinc-600 hover:text-zinc-300">
          <Kbd>esc</Kbd>
        </button>
      </header>

      <div className="px-3 pt-2">
        <Row k="Direction" v={<span className={trade.side === "BUY" ? "text-emerald-400" : "text-rose-400"}>{trade.side} {trade.commodity_type}</span>} />
        <Row k="Counterparty" v={trade.counterparty_name} />
        <Row k="LEI" v={trade.counterparty_lei} />
        <Row k="Volume" v={`${fmt.num(trade.volume)} ${trade.unit}`} />
        <Row k="Price" v={`${fmt.num(trade.price, 4)} ${trade.currency}`} />
        <Row k="Notional" v={fmt.money(trade.notional, trade.currency)} />
        <Row k="Settlement" v={trade.settlement_date} />
        <Row k="Delivery account" v={trade.delivery_account} />
        <Row k="Booked by" v={trade.trader_id} />
      </div>

      {trade.goo && (
        <Section title="EECS certificate">
          <Row k="Technology" v={fmt.label(trade.goo.technology)} />
          <Row k="Production" v={`${trade.goo.vintage_start} → ${trade.goo.vintage_end}`} />
          <Row k="RED II expiry" v={<span className="text-amber-300">{trade.goo.expiry}</span>} />
          <Row k="Domain" v={trade.goo.eecs_domain} />
          <Row
            k="Support scheme"
            v={
              <span className={trade.goo.is_supported ? "text-amber-300" : "text-zinc-300"}>
                {trade.goo.is_supported ? "SUPPORTED" : "UNSUPPORTED"}
              </span>
            }
          />
          <Row k="Commissioned" v={trade.goo.commissioning_date} />
          <Row k="Issuing body" v={trade.goo.issuing_body} />
        </Section>
      )}

      {trade.eua && (
        <Section title="EU ETS allowance">
          <Row k="Compliance year" v={trade.eua.compliance_year} />
          <Row k="Phase" v={trade.eua.surrender_phase} />
          <Row k="Union Registry" v={trade.eua.registry_account} />
        </Section>
      )}

      {(trade.registry_transfer_ref || trade.settlement_reference || trade.retirement_beneficiary) && (
        <Section title="Settlement evidence">
          {trade.registry_transfer_ref && <Row k="Registry transfer" v={trade.registry_transfer_ref} />}
          {trade.settlement_reference && <Row k="Payment" v={trade.settlement_reference} />}
          {trade.retirement_beneficiary && <Row k="Beneficiary" v={trade.retirement_beneficiary} />}
        </Section>
      )}

      <h3 className="mb-0 mt-2 px-3 text-[10px] font-semibold uppercase tracking-[0.06em] text-zinc-500">
        Pipeline
      </h3>
      <Rail status={trade.lifecycle_status} />

      <div className="px-3 pb-3">
        {trade.lifecycle_status !== "BOOKED" && (
          <a
            href={api.confirmationUrl(trade.id)}
            target="_blank"
            rel="noreferrer"
            className="mb-2 block rounded border border-desk-700 py-1.5 text-center text-[11px] text-zinc-300 hover:border-zinc-500 hover:text-zinc-100"
          >
            EFET Individual Contract Confirmation · PDF
          </a>
        )}

        {actions.length > 0 && (
          <div className="space-y-2 rounded border border-desk-700 bg-desk-850 p-2">
            <div>
              <span className="mb-1 block text-[10px] uppercase tracking-[0.06em] text-zinc-600">
                Operator
              </span>
              <input
                value={operator}
                onChange={(e) => setOperator(e.target.value)}
                className="w-full rounded border border-desk-700 bg-desk-900 px-2 py-1 text-[11px] outline-none focus:border-zinc-500"
              />
            </div>
            {actions.map((a) => (
              <div key={a.to} className="space-y-1">
                {a.evidence && (
                  <input
                    value={evidence[a.evidence] ?? ""}
                    onChange={(e) => setEvidence((s) => ({ ...s, [a.evidence!]: e.target.value }))}
                    placeholder={EVIDENCE_LABEL[a.evidence]}
                    className="num w-full rounded border border-desk-700 bg-desk-900 px-2 py-1 text-[11px] outline-none placeholder:font-sans placeholder:text-zinc-600 focus:border-zinc-500"
                  />
                )}
                <button
                  disabled={busy || (!!a.evidence && !evidence[a.evidence])}
                  onClick={() => advance(a.to)}
                  className="w-full rounded bg-zinc-100 py-1.5 text-[11px] font-semibold text-zinc-900 hover:bg-white disabled:cursor-not-allowed disabled:opacity-25"
                >
                  {a.verb}
                </button>
              </div>
            ))}
          </div>
        )}

        {trade.lifecycle_status === "BOOKED" && (
          <p className="rounded border border-amber-900/50 bg-amber-950/20 p-2 text-[10px] leading-4 text-amber-300/90">
            Rendering the EFET confirmation. This trade advances to CONFIRMATION PENDING
            on its own and the blotter updates over the socket.
          </p>
        )}

        {trade.lifecycle_status === "RETIRED" && (
          <p className="rounded border border-desk-700 bg-desk-850 p-2 text-[10px] leading-4 text-zinc-500">
            Certificates cancelled in the registry for{" "}
            <span className="text-zinc-300">{trade.retirement_beneficiary}</span>. They can no
            longer circulate in the secondary market.
          </p>
        )}

        {error && (
          <pre className="mt-2 whitespace-pre-wrap rounded border border-rose-900/70 bg-rose-950/40 p-2 text-[10px] leading-4 text-rose-300">
            {error}
          </pre>
        )}
      </div>

      <Section title={`Audit trail · ${audit.length}`}>
        <ol className="space-y-px">
          {audit.map((a) => (
            <li key={a.id} className="flex justify-between gap-2 border-b border-desk-900 py-1">
              <span className="truncate text-[10px] text-zinc-300">{fmt.label(a.action)}</span>
              <span className="num shrink-0 text-[10px] text-zinc-600">
                {a.performed_by} · {new Date(a.logged_at).toLocaleString("en-GB", { hour12: false })}
              </span>
            </li>
          ))}
        </ol>
      </Section>
    </aside>
  );
}
