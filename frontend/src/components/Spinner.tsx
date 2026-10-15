export function Spinner({ className = '' }: { className?: string }) {
  return (
    <div
      data-testid="spinner"
      className={`inline-block h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700 ${className}`}
      role="status"
      aria-label="Loading"
    />
  )
}

export function PageSpinner() {
  return (
    <div className="flex min-h-64 items-center justify-center">
      <Spinner className="h-8 w-8" />
    </div>
  )
}
