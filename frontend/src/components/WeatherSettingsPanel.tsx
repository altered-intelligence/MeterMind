import { useEffect, useState } from "react";
import { api, type WeatherLocation } from "../api";
import { Card, InfoTip } from "./ui";

const TIP =
  "Weather adds context to usage (e.g. kWh per cooling degree-day). Enter a ZIP code and " +
  "we'll look up the coordinates, or enter latitude/longitude directly. No street address is stored.";

type Mode = "zip" | "coords";

export default function WeatherSettingsPanel({
  version,
  onChanged,
}: {
  version: number;
  onChanged?: () => void;
}) {
  const [loc, setLoc] = useState<WeatherLocation | null>(null);
  const [mode, setMode] = useState<Mode>("zip");
  const [zip, setZip] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .getWeatherLocation()
      .then((l) => {
        if (!alive) return;
        setLoc(l);
        if (l) {
          setLat(String(l.latitude));
          setLon(String(l.longitude));
          setLabel(l.label ?? "");
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [version]);

  async function save() {
    setError(null);
    setSaved(false);

    let body: Parameters<typeof api.setWeatherLocation>[0];
    if (mode === "zip") {
      if (!zip.trim()) {
        setError("Enter a ZIP/postal code.");
        return;
      }
      body = { zip_code: zip.trim(), label: label.trim() || undefined };
    } else {
      const latitude = Number(lat);
      const longitude = Number(lon);
      if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
        setError("Latitude must be between -90 and 90.");
        return;
      }
      if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) {
        setError("Longitude must be between -180 and 180.");
        return;
      }
      body = { latitude, longitude, label: label.trim() || undefined };
    }

    setBusy(true);
    try {
      const next = await api.setWeatherLocation(body);
      setLoc(next);
      setLat(String(next.latitude));
      setLon(String(next.longitude));
      setLabel(next.label ?? "");
      setSaved(true);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save location.");
    } finally {
      setBusy(false);
    }
  }

  const tab = (m: Mode, text: string) => (
    <button
      onClick={() => {
        setMode(m);
        setError(null);
      }}
      className={`rounded-md px-2.5 py-1 text-xs font-medium ${
        mode === m ? "bg-sky-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
      }`}
    >
      {text}
    </button>
  );

  const inputCls =
    "mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-sky-300";

  return (
    <Card title="Weather location" right={<InfoTip text={TIP} />}>
      {loc ? (
        <p className="mb-3 text-xs text-slate-500">
          Configured: <span className="font-medium text-slate-700">{loc.label || "location"}</span>{" "}
          ({loc.latitude.toFixed(4)}, {loc.longitude.toFixed(4)}) · {loc.timezone}
        </p>
      ) : (
        <p className="mb-3 text-xs text-slate-500">
          No location set yet. Enter a ZIP code (or coordinates) to enable weather context.
        </p>
      )}

      <div className="mb-3 flex gap-2">
        {tab("zip", "ZIP code")}
        {tab("coords", "Coordinates")}
      </div>

      {mode === "zip" ? (
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs text-slate-500">
            ZIP / postal code
            <input
              value={zip}
              onChange={(e) => setZip(e.target.value)}
              inputMode="numeric"
              placeholder="76227"
              className={inputCls}
            />
          </label>
          <label className="text-xs text-slate-500">
            Label (optional)
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Home" className={inputCls} />
          </label>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <label className="text-xs text-slate-500">
            Latitude
            <input value={lat} onChange={(e) => setLat(e.target.value)} inputMode="decimal" placeholder="33.0700" className={inputCls} />
          </label>
          <label className="text-xs text-slate-500">
            Longitude
            <input value={lon} onChange={(e) => setLon(e.target.value)} inputMode="decimal" placeholder="-96.9000" className={inputCls} />
          </label>
          <label className="text-xs text-slate-500">
            Label (optional)
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Home" className={inputCls} />
          </label>
        </div>
      )}

      {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
      {saved && <p className="mt-2 text-xs text-emerald-600">Saved. Sync a bill to pull its weather.</p>}

      <div className="mt-3">
        <button
          onClick={save}
          disabled={busy}
          className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-sky-300"
        >
          {busy ? "Saving…" : mode === "zip" ? "Look up & save" : loc ? "Update location" : "Save location"}
        </button>
      </div>
    </Card>
  );
}
