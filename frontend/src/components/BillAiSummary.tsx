import { useEffect, useState } from "react";
import { api, type BillSummaryResponse } from "../api";
import { Card } from "./ui";

const PRIVACY_NOTE =
  "Only redacted metrics are sent to the model — never the PDF, raw text, or any account / meter / address.";

type Phase = "loading" | "streaming" | "done" | "error" | "idle";

function DisabledNote() {
  return (
    <div className="text-sm text-slate-500">
      <p>
        Optional local-LLM summaries are <span className="font-medium">off</span>. To enable, run{" "}
        <a href="https://ollama.com" className="text-sky-600 underline" target="_blank" rel="noreferrer">
          Ollama
        </a>{" "}
        and start the backend with <code className="rounded bg-slate-100 px-1">OLLAMA_ENABLED=true</code>.
      </p>
      <p className="mt-1 text-xs text-slate-400">
        Every number in this app is computed deterministically — the LLM only rephrases the figures
        it's given.
      </p>
    </div>
  );
}

export default function BillAiSummary({ billId, version }: { billId: number; version: number }) {
  const [meta, setMeta] = useState<BillSummaryResponse | null>(null);
  const [text, setText] = useState("");
  const [phase, setPhase] = useState<Phase>("loading");

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    setMeta(null);
    setText("");
    setPhase("loading");

    (async () => {
      // 1) Fast: deterministic summary + metadata (never blocks on the model).
      let data: BillSummaryResponse;
      try {
        data = await api.billSummary(billId);
      } catch {
        if (alive) setPhase("error");
        return;
      }
      if (!alive) return;
      setMeta(data);
      if (!data.llm_enabled) {
        setPhase("idle");
        return;
      }

      // 2) Stream the LLM tokens in as they arrive.
      setPhase("streaming");
      let acc = "";
      try {
        const res = await fetch(`/api/bills/${billId}/summary/stream`, { signal: controller.signal });
        if (!res.ok || !res.body) {
          if (alive) setPhase("error");
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          acc += decoder.decode(value, { stream: true });
          if (alive) setText(acc);
        }
        if (alive) setPhase(acc ? "done" : "error");
      } catch {
        if (alive) setPhase(acc ? "done" : "error");
      }
    })();

    return () => {
      alive = false;
      controller.abort();
    };
  }, [billId, version]);

  if (!meta) {
    return (
      <Card title="AI summary (optional, local)">
        <p className="text-sm text-slate-400">{phase === "error" ? "Summary unavailable." : "Loading…"}</p>
      </Card>
    );
  }

  return (
    <Card title="AI summary (optional, local)">
      {!meta.llm_enabled ? (
        <DisabledNote />
      ) : text ? (
        <div>
          <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
            {text}
            {phase === "streaming" && <span className="ml-0.5 animate-pulse">▍</span>}
          </div>
          <p className="mt-2 text-xs text-slate-400">
            Generated locally by <span className="font-medium">{meta.llm_model}</span> from redacted
            metrics. Figures come from the deterministic parser.
          </p>
        </div>
      ) : phase === "streaming" ? (
        <p className="text-sm text-slate-500">
          Generating a local summary with <span className="font-medium">{meta.llm_model}</span>…{" "}
          <span className="animate-pulse">▍</span>
          <br />
          <span className="text-xs text-slate-400">
            The rest of the dashboard is ready — this fills in when the model responds.
          </span>
        </p>
      ) : (
        <div className="text-sm text-slate-600">
          <p>
            Local model <span className="font-medium">{meta.llm_model}</span> was unreachable. Showing
            the deterministic summary:
          </p>
          <p className="mt-2 leading-relaxed text-slate-700">{meta.deterministic_summary}</p>
        </div>
      )}
      <p className="mt-3 border-t border-slate-100 pt-2 text-[11px] text-slate-400">{PRIVACY_NOTE}</p>
    </Card>
  );
}
