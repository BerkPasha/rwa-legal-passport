import { Link, useLocation } from 'react-router-dom'

// Any address that is not a page of the application (Stage 10B-2): the navigation stays available
export function NotFound() {
  const { pathname } = useLocation()
  return (
    <section aria-labelledby="not-found-heading" className="mx-auto max-w-xl rounded-xl bg-white p-8 text-center shadow-sm ring-1 ring-slate-200">
      <p className="text-sm font-semibold text-indigo-600">404</p>
      <h2 id="not-found-heading" className="mt-2 text-2xl font-bold text-slate-900">Page not found</h2>
      <p className="mt-3 text-sm text-slate-600">
        The address <span className="break-all font-mono text-slate-800">{pathname}</span> is not a page of the
        RWA Legal Passport. Nothing was loaded from the server.
      </p>
      <div className="mt-6 flex flex-wrap justify-center gap-3">
        <Link to="/dashboard" className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
          Go to the dashboard
        </Link>
        <Link to="/assets" className="rounded-lg bg-slate-100 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-200">
          Open the asset registry
        </Link>
      </div>
    </section>
  )
}
