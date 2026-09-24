import { useEffect, useState } from "react";
import { api, fmt, type Position, type Vwap } from "./api";
import { Bar, Empty } from "./ui";

const th = "px-3 py-1.5 text-left text-[10px] font-semibold uppercase tracking-[0.06em] text-zinc-600";
const td = "px-3 py-1.5 text-[11px]";

export default function Positions({ refreshKey }: { refreshKey: number }) {
  const [positions, setPositions] = useState<Position[]>([]);
  const [vwap, setVwap] = useState<Vwap | null>(null);

  useEffect(() => {
    api.positions().then(setPositions).catch(() => {});
    api.vwap(new Date().getFullYear()).then(setVwap).catch(() => {});
  }, [refreshKey]);

  const maxExposure = Math.max(1, ...positions.map((p) => Math.abs(Number(p.net_cash_exposure))));
  const maxVwap = Math.max(1e-9, ...(vwap?.technologies ?? []).map((t) => Number(t.vwap)));

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="grid grid-cols-1 gap-px bg-desk-800 xl:grid-cols-[1.4fr_1fr]">
        <section className="bg-desk-950">
          <header className="border-b border-desk-800 px-3 py-2">
            <h2 className="text-[11px] font-semibold text-zinc-200">Net Open Position</h2>
            <p className="text-[10px] text-zinc-600">
              Undelivered exposure by counterparty and commodity. Excludes SETTLED and RETIRED.
            </p>
          </header>
          {positions.length === 0 ? (
            <Empty title="Flat. No open positions." />
          ) : (
            <table className="w-full">
              <thead className="border-b border-desk-800">
                <tr>
                  <th className={th}>Counterparty</th>
                  <th className={th}>Product</th>
                  <th className={`${th} text-right`}>Trades</th>
                  <th className={`${th} text-right`}>Net volume</th>
                  <th className={`${th} text-right`}>Net exposure</th>
                  <th className={`${th} w-32`}>Weight</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => {
                  const exposure = Number(p.net_cash_exposure);
                  const long = exposure >= 0;
                  return (
                    <tr key={i} className="border-b border-desk-900 hover:bg-desk-900">
                      <td className={`${td} text-zinc-300`}>{p.counterparty_name}</td>
                      <td className={`${td} text-zinc-500`}>{p.commodity_type}</td>
                      <td className={`${td} num text-right text-zinc-600`}>{p.trade_count}</td>
                      <td
                        className={`${td} num text-right ${long ? "text-emerald-400" : "text-rose-400"}`}
                      >
                        {fmt.num(p.net_open_volume)}
                        <span className="ml-1 text-[10px] text-zinc-600">{p.unit}</span>
                      </td>
                      <td
                        className={`${td} num text-right ${long ? "text-emerald-400" : "text-rose-400"}`}
                      >
                        {fmt.num(exposure, 2)}
                        <span className="ml-1 text-[10px] text-zinc-600">{p.currency}</span>
                      </td>
                      <td className={td}>
                        <Bar
                          value={exposure}
                          max={maxExposure}
                          tone={long ? "bg-emerald-500/70" : "bg-rose-500/70"}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>

        <section className="bg-desk-950">
          <header className="border-b border-desk-800 px-3 py-2">
            <h2 className="text-[11px] font-semibold text-zinc-200">
              Technology VWAP — vintage {vwap?.vintage_year ?? "—"}
            </h2>
            <p className="text-[10px] text-zinc-600">
              Volume-weighted execution price per generation source. The spread between
              these is the desk's core relative-value signal.
            </p>
          </header>
          {!vwap?.technologies.length ? (
            <Empty title="No GOO executions in this vintage year." />
          ) : (
            <>
              <table className="w-full">
                <thead className="border-b border-desk-800">
                  <tr>
                    <th className={th}>Technology</th>
                    <th className={`${th} text-right`}>Trades</th>
                    <th className={`${th} text-right`}>Volume MWh</th>
                    <th className={`${th} text-right`}>VWAP</th>
                    <th className={`${th} w-28`} />
                  </tr>
                </thead>
                <tbody>
                  {vwap.technologies.map((t) => (
                    <tr key={t.technology} className="border-b border-desk-900 hover:bg-desk-900">
                      <td className={`${td} text-zinc-300`}>{fmt.label(t.technology)}</td>
                      <td className={`${td} num text-right text-zinc-600`}>{t.trade_count}</td>
                      <td className={`${td} num text-right text-zinc-400`}>{fmt.num(t.volume)}</td>
                      <td className={`${td} num text-right text-zinc-100`}>{fmt.num(t.vwap, 4)}</td>
                      <td className={td}>
                        <Bar value={Number(t.vwap)} max={maxVwap} tone="bg-zinc-500" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {vwap.solar_to_hydro_spread && (
                <div className="m-3 rounded border border-desk-800 bg-desk-900 p-3">
                  <p className="text-[10px] uppercase tracking-[0.06em] text-zinc-600">
                    Solar PV − Hydro Run-of-River
                  </p>
                  <p className="num text-lg text-zinc-100">
                    {fmt.num(vwap.solar_to_hydro_spread, 4)}
                    <span className="ml-1 text-[11px] text-zinc-500">EUR / MWh</span>
                  </p>
                  <p className="mt-1 text-[10px] text-zinc-600">
                    Solar {fmt.num(vwap.solar_vwap ?? 0, 4)} · Hydro RoR{" "}
                    {fmt.num(vwap.hydro_run_of_river_vwap ?? 0, 4)}
                  </p>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
