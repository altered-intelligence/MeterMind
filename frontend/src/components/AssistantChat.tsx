import { useEffect, useRef, useState } from "react";
import { api, type ModelsInfo } from "../api";
import { Card } from "./ui";

interface Msg {
  role: "user" | "assistant";
  content: string;
  thinking?: string;
}

const SUGGESTIONS = [
  "Which month had my highest bill?",
  "How has my electric usage changed over the past year?",
  "What's my average gas cost per day?",
  "Compare my latest bill to my rolling average.",
];

function ThinkingBox({ text, live }: { text: string; live: boolean }) {
  if (!text) return null;
  if (live) {
    return (
      <div className="mb-1 rounded-md border border-slate-200 bg-slate-50 p-2">
        <div className="mb-1 text-[11px] font-medium text-slate-500">
          Thinking… <span className="animate-pulse">▍</span>
        </div>
        <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap text-[11px] leading-snug text-slate-500">
          {text}
        </pre>
      </div>
    );
  }
  return (
    <details className="mb-1">
      <summary className="cursor-pointer text-[11px] text-slate-400">Show reasoning</summary>
      <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md bg-slate-50 p-2 text-[11px] leading-snug text-slate-500">
        {text}
      </pre>
    </details>
  );
}

export default function AssistantChat({ version }: { version: number }) {
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<number | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .assistantModels()
      .then((m) => alive && setModels(m))
      .catch(() => alive && setModels(null));
    return () => {
      alive = false;
    };
  }, [version]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  function patchLast(patch: Partial<Msg>) {
    setMessages((m) => {
      if (!m.length) return m;
      const copy = [...m];
      copy[copy.length - 1] = { ...copy[copy.length - 1], ...patch };
      return copy;
    });
  }

  async function changeModel(name: string) {
    if (busy || !models || name === models.active) return;
    try {
      const r = await api.setAssistantModel(name);
      setModels({ ...models, active: r.active });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not switch model.");
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setError(null);
    setStatus("Sending…");
    const history = [...messages, { role: "user", content: q } as Msg];
    setMessages([...history, { role: "assistant", content: "", thinking: "" }]);
    setInput("");
    setBusy(true);
    setElapsed(0);
    timerRef.current = window.setInterval(() => setElapsed((e) => e + 1), 1000);

    const controller = new AbortController();
    abortRef.current = controller;
    let thinkAcc = "";
    let ansAcc = "";
    try {
      const res = await fetch("/api/assistant/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        throw new Error(res.status === 409 ? "Local AI chat is disabled." : `Chat failed (HTTP ${res.status}).`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl: number;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          let ev: { t: string; d: string };
          try {
            ev = JSON.parse(line);
          } catch {
            continue;
          }
          if (ev.t === "think") {
            thinkAcc += ev.d;
            patchLast({ thinking: thinkAcc });
          } else if (ev.t === "answer") {
            ansAcc += ev.d;
            patchLast({ content: ansAcc });
          } else if (ev.t === "status") {
            setStatus(ev.d);
          }
        }
      }
      if (!ansAcc && !thinkAcc) {
        setError("No response — the local model may be unavailable.");
        setMessages((m) => m.slice(0, -1));
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError" && !ansAcc && !thinkAcc) {
        setError(e instanceof Error ? e.message : "Chat failed.");
        setMessages((m) => m.slice(0, -1));
      }
    } finally {
      setBusy(false);
      setStatus("");
      abortRef.current = null;
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
  }

  if (!models) return null;

  const modelPicker =
    models.enabled && models.available.length > 0 ? (
      <select
        value={models.active}
        onChange={(e) => changeModel(e.target.value)}
        disabled={busy}
        title="Local model"
        className="max-w-[12rem] rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700 disabled:opacity-50"
      >
        {models.available.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    ) : null;

  if (!models.enabled) {
    return (
      <Card title="Ask about your data">
        <p className="text-sm text-slate-500">
          Local AI chat is <span className="font-medium">off</span>. Start the backend with{" "}
          <code className="rounded bg-slate-100 px-1">OLLAMA_ENABLED=true</code> (and Ollama running) to
          ask questions about your bills.
        </p>
      </Card>
    );
  }

  const lastIdx = messages.length - 1;

  return (
    <Card title="Ask about your data" right={modelPicker}>
      <div className="mb-3 max-h-[28rem] space-y-3 overflow-y-auto">
        {messages.length === 0 ? (
          <p className="text-sm text-slate-500">
            Ask anything about your utility history — the assistant reads all your bills.
          </p>
        ) : (
          messages.map((m, i) => {
            const isStreaming = busy && i === lastIdx && m.role === "assistant";
            if (m.role === "user") {
              return (
                <div key={i} className="text-right">
                  <div className="inline-block max-w-[85%] whitespace-pre-wrap rounded-lg bg-sky-600 px-3 py-2 text-sm text-white">
                    {m.content}
                  </div>
                </div>
              );
            }
            return (
              <div key={i} className="text-left">
                <ThinkingBox text={m.thinking || ""} live={isStreaming} />
                {m.content ? (
                  <div className="inline-block max-w-[90%] whitespace-pre-wrap rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-700">
                    {m.content}
                    {isStreaming && <span className="animate-pulse">▍</span>}
                  </div>
                ) : (
                  isStreaming && (
                    <div className="text-xs text-slate-500">
                      <span className="mr-1 inline-block animate-pulse">●</span>
                      {status || "Working"} · {elapsed}s
                    </div>
                  )
                )}
              </div>
            );
          })
        )}
        <div ref={endRef} />
      </div>

      {messages.length === 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => send(s)}
              disabled={busy}
              className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:border-sky-300 hover:text-sky-700 disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={busy ? "Working…" : "Ask a question about your bills…"}
          disabled={busy}
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-sky-400 focus:outline-none focus:ring-1 focus:ring-sky-300 disabled:bg-slate-50"
        />
        {busy ? (
          <button
            type="button"
            onClick={stop}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!input.trim()}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50"
          >
            Send
          </button>
        )}
        {messages.length > 0 && !busy && (
          <button
            type="button"
            onClick={() => {
              setMessages([]);
              setError(null);
            }}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-500 hover:bg-slate-50"
          >
            Clear
          </button>
        )}
      </form>

      {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
      <p className="mt-3 border-t border-slate-100 pt-2 text-[11px] text-slate-400">
        Answers come from a local model ({models.active}) reading only redacted metrics from your
        sources. No PDF, raw text, or personal info is sent. It can be wrong — verify against the
        figures shown elsewhere.
      </p>
    </Card>
  );
}
