/** Reusable dismissible error banner with an optional retry action. */
export default function ErrorBanner({
  message,
  onRetry,
  onDismiss,
}: {
  message: string | null;
  onRetry?: () => void;
  onDismiss?: () => void;
}) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"
    >
      <span>{message}</span>
      <div className="flex items-center gap-2">
        {onRetry && (
          <button
            onClick={onRetry}
            className="rounded-md border border-rose-300 px-2 py-1 text-xs font-medium hover:bg-rose-100 focus:outline-none focus:ring-2 focus:ring-rose-400"
          >
            Retry
          </button>
        )}
        {onDismiss && (
          <button
            onClick={onDismiss}
            aria-label="Dismiss"
            className="rounded-md px-2 py-1 text-xs text-rose-500 hover:text-rose-700 focus:outline-none focus:ring-2 focus:ring-rose-400"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
