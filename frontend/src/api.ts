export const LIFECYCLE = [
  "BOOKED",
  "CONFIRMATION_PENDING",
  "CONFIRMED",
  "REGISTRY_TRANSFER_INITIATED",
  "DELIVERED",
  "SETTLED",
  "RETIRED",
] as const;
export type Lifecycle = (typeof LIFECYCLE)[number];

export const TECHNOLOGIES = [
  "HYDRO_RUN_OF_RIVER",
  "HYDRO_RESERVOIR",
  "SOLAR_PV",
  "WIND_ONSHORE",
  "WIND_OFFSHORE",
  "BIOMASS",
] as const;

export type Trade = {
  id: string;
  trade_reference: string;
  commodity_type: "GOO" | "EUA" | "REGO";
  counterparty_id: string;
  counterparty_name: string;
  counterparty_lei: string;
  trader_id: string;
  side: "BUY" | "SELL";
  price: string;
  volume: string;
  unit: "MWH" | "TCO2";
  currency: "EUR" | "GBP";
  notional: string;
  trade_timestamp: string;
  settlement_date: string;
  lifecycle_status: Lifecycle;
  registry_transfer_ref: string | null;
  settlement_reference: string | null;
  retirement_beneficiary: string | null;
  delivery_account: string | null;
  goo: {
    vintage_start: string;
    vintage_end: string;
    expiry: string;
    technology: string;
    country_of_origin: string;
    eecs_domain: string;
    is_supported: boolean;
    commissioning_date: string | null;
    issuing_body: string;
  } | null;
  eua: { compliance_year: number; surrender_phase: string; registry_account: string } | null;
};

export type Counterparty = {
  id: string;
  legal_name: string;
  lei: string;
  country: string;
  verticer_account_id: string | null;
  hknr_account_id: string | null;
  grexel_account_id: string | null;
  ofgem_account_id: string | null;
  union_registry_account_id: string | null;
};

export type AuditEntry = {
  id: number;
  action: string;
  performed_by: string;
  logged_at: string;
  previous_state: Record<string, unknown> | null;
  new_state: Record<string, unknown>;
};

export type Position = {
  commodity_type: string;
  counterparty_name: string;
  unit: string;
  currency: string;
  trade_count: number;
  net_open_volume: string;
  net_cash_exposure: string;
};

export type Vwap = {
  vintage_year: number;
  technologies: { technology: string; volume: string; trade_count: number; vwap: string }[];
  solar_vwap: string | null;
  hydro_run_of_river_vwap: string | null;
  solar_to_hydro_spread: string | null;
};

export type Expiring = {
  id: string;
  trade_reference: string;
  side: "BUY" | "SELL";
  counterparty_name: string;
  technology: string;
  volume: string;
  expiry: string;
  days_remaining: number;
  lifecycle_status: Lifecycle;
};

/** Surfaces FastAPI/Pydantic rejections as readable desk errors instead of "[object Object]". */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(formatDetail(body.detail) || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: any) => {
        const field = (d.loc ?? []).filter((p: unknown) => p !== "body").join(".");
        return field ? `${field}: ${d.msg}` : d.msg;
      })
      .join("\n");
  }
  return "";
}

export const api = {
  trades: (open?: boolean) => call<Trade[]>(`/trades${open ? "?open_only=true" : ""}`),
  counterparties: () => call<Counterparty[]>("/counterparties"),
  createTrade: (payload: unknown) =>
    call<Trade>("/trades", { method: "POST", body: JSON.stringify(payload) }),
  transition: (id: string, payload: unknown) =>
    call<Trade>(`/trades/${id}/transition`, { method: "POST", body: JSON.stringify(payload) }),
  amend: (id: string, params: Record<string, string>) =>
    call<Trade>(`/trades/${id}?${new URLSearchParams(params)}`, { method: "PATCH" }),
  audit: (id: string) => call<AuditEntry[]>(`/trades/${id}/audit`),
  positions: () => call<Position[]>("/analytics/positions"),
  vwap: (year: number) => call<Vwap>(`/analytics/tech-vwap?vintage_year=${year}`),
  expiring: () => call<Expiring[]>("/analytics/expiring"),
  confirmationUrl: (id: string) => `/api/v1/trades/${id}/confirmation.pdf`,
};

export const fmt = {
  num: (v: string | number, dp = 0) =>
    Number(v).toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp }),
  money: (v: string | number, ccy: string) =>
    `${Number(v).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${ccy}`,
  time: (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour12: false }),
  label: (s: string) => s.replaceAll("_", " "),
};
