interface DisclaimerBannerProps {
  text: string
  variant?: 'warning' | 'legal'
}

export function DisclaimerBanner({ text, variant = 'warning' }: DisclaimerBannerProps) {
  const styles =
    variant === 'legal'
      ? 'bg-red-50 border-red-200 text-red-800'
      : 'bg-amber-50 border-amber-200 text-amber-800'

  return (
    <div
      data-testid="disclaimer-banner"
      className={`rounded-lg border p-4 ${styles}`}
      role="alert"
    >
      <div className="flex gap-3">
        <svg
          className="mt-0.5 h-5 w-5 shrink-0"
          viewBox="0 0 20 20"
          fill="currentColor"
          aria-hidden="true"
        >
          <path
            fillRule="evenodd"
            d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z"
            clipRule="evenodd"
          />
        </svg>
        <p className="text-sm font-medium leading-5">{text}</p>
      </div>
    </div>
  )
}
