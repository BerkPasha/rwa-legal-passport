import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Landing } from './pages/Landing'

// The read-only product demo: its own chunk, loaded only when /demo is opened (and not emitted at all by
// the application build, which never renders this component)
const DemoApp = /* @__PURE__ */ lazy(() => import('./demo/DemoApp'))

// The public website build (site.ts): the landing page and the read-only demo at /demo. Any other address
// leads back to the landing page.
export function PublicSite() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route
        path="/demo/*"
        element={
          <Suspense fallback={<div className="h-screen bg-paper" aria-busy="true" />}>
            <DemoApp />
          </Suspense>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
