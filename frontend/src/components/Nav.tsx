export type View = "insights" | "upload" | "dashboard" | "electric" | "gas" | "trends" | "debug";

export const NAV_ITEMS: { id: View; label: string }[] = [
  { id: "insights", label: "Insights" },
  { id: "dashboard", label: "Dashboard" },
  { id: "electric", label: "Electric" },
  { id: "gas", label: "Gas" },
  { id: "trends", label: "Trends" },
  { id: "upload", label: "Upload bill" },
  { id: "debug", label: "Parser debug" },
];

export default function Nav({
  view,
  onSelect,
  orientation,
}: {
  view: View;
  onSelect: (v: View) => void;
  orientation: "vertical" | "horizontal";
}) {
  const vertical = orientation === "vertical";
  return (
    <nav
      className={
        vertical
          ? "flex flex-col gap-1 p-2"
          : "flex gap-1 overflow-x-auto p-2"
      }
    >
      {NAV_ITEMS.map((item) => {
        const active = view === item.id;
        return (
          <button
            key={item.id}
            onClick={() => onSelect(item.id)}
            aria-current={active ? "page" : undefined}
            className={`whitespace-nowrap rounded-md px-3 py-2 text-left text-sm font-medium transition ${
              active
                ? "bg-sky-50 text-sky-700 ring-1 ring-sky-200"
                : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}
