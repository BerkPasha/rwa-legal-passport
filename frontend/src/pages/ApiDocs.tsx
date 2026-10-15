export function ApiDocs() {
  const base = 'http://localhost:8000'

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-bold text-slate-900">API Reference</h2>
        <p className="mt-1 text-sm text-slate-500">
          RWA Legal Passport — Institutional Legal Transparency API
        </p>
      </div>

      {/* Interactive docs link */}
      <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-5">
        <div className="flex items-center gap-3">
          <svg className="h-6 w-6 shrink-0 text-indigo-600" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M17.25 6.75L22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3l-4.5 16.5" />
          </svg>
          <div>
            <p className="font-semibold text-indigo-900">Interactive API Documentation</p>
            <p className="mt-0.5 text-sm text-indigo-700">
              Full Swagger UI available at{' '}
              <code className="rounded bg-indigo-100 px-1.5 py-0.5 font-mono text-xs">{base}/docs</code>
              {' '}and ReDoc at{' '}
              <code className="rounded bg-indigo-100 px-1.5 py-0.5 font-mono text-xs">{base}/redoc</code>
            </p>
          </div>
        </div>
      </div>

      {/* Authentication */}
      <Section title="Authentication">
        <div className="space-y-4">
          <AuthCard
            name="Reviewer Token"
            header="Authorization: Bearer <token>"
            description="Required for review workflow endpoints (start review, correct claims, approve). Internal reviewers only."
            configuredIn="REVIEWER_ISSUER_TOKEN / REVIEWER_LEGAL_TOKEN"
          />
          <AuthCard
            name="Institutional API Key"
            header="X-API-Key: <key>"
            description="Required for the public Legal Passport endpoint. Rate-limited to 60 requests/minute per key."
            configuredIn="INSTITUTIONAL_API_KEY_ALPHA / INSTITUTIONAL_API_KEY_BETA"
          />
        </div>
      </Section>

      {/* Endpoint groups */}
      <Section title="Asset Registry">
        <EndpointTable endpoints={[
          { method: 'GET', path: '/assets', auth: 'none', description: 'List all registered tokenized assets' },
          { method: 'POST', path: '/assets', auth: 'none', description: 'Register a new tokenized asset' },
          { method: 'GET', path: '/assets/{asset_id}', auth: 'none', description: 'Get asset details' },
          { method: 'PATCH', path: '/assets/{asset_id}', auth: 'none', description: 'Update asset status or metadata' },
        ]} />
      </Section>

      <Section title="Document Management">
        <EndpointTable endpoints={[
          { method: 'GET', path: '/assets/{asset_id}/documents', auth: 'none', description: 'List all documents for an asset' },
          { method: 'POST', path: '/assets/{asset_id}/documents', auth: 'none', description: 'Upload a new legal document (multipart/form-data)' },
          { method: 'GET', path: '/documents/{doc_id}/versions', auth: 'none', description: 'List all versions of a document' },
        ]} />
      </Section>

      <Section title="AI Extraction">
        <EndpointTable endpoints={[
          { method: 'POST', path: '/documents/{doc_id}/extractions', auth: 'none', description: 'Trigger AI extraction on a document version' },
          { method: 'GET', path: '/documents/{doc_id}/extractions', auth: 'none', description: 'List all extractions for a document' },
          { method: 'GET', path: '/extractions/{extraction_id}', auth: 'none', description: 'Get extraction details with all claims' },
        ]} />
      </Section>

      <Section title="Review Workflow">
        <EndpointTable endpoints={[
          { method: 'POST', path: '/extractions/{extraction_id}/review', auth: 'Bearer', description: 'Start review — moves status to under_review' },
          { method: 'PATCH', path: '/extractions/{extraction_id}/claims/{claim_id}', auth: 'Bearer', description: 'Correct a legal claim value' },
          { method: 'POST', path: '/extractions/{extraction_id}/approvals', auth: 'Bearer', description: 'Approve extraction (moves to approved)' },
          { method: 'GET', path: '/extractions/{extraction_id}/corrections', auth: 'Bearer', description: 'List all corrections for an extraction' },
          { method: 'GET', path: '/extractions/{extraction_id}/approvals', auth: 'Bearer', description: 'List all approvals for an extraction' },
        ]} />
      </Section>

      <Section title="Blockchain Status">
        <EndpointTable endpoints={[
          { method: 'GET', path: '/assets/{asset_id}/blockchain-status', auth: 'none', description: 'Query Stellar Horizon for on-chain asset status' },
        ]} />
      </Section>

      <Section title="Legal Passport (Institutional)">
        <EndpointTable endpoints={[
          { method: 'GET', path: '/passport/{network}/{asset_code}/{issuer_key}', auth: 'X-API-Key', description: 'Retrieve the public legal transparency passport for an approved asset' },
        ]} />
      </Section>

      {/* Code examples */}
      <Section title="Code Examples">
        <div className="space-y-6">
          <CodeExample
            title="Fetch Legal Passport — curl"
            language="bash"
            code={`curl -H "X-API-Key: <your-api-key>" \\
  "${base}/passport/stellar-testnet/MRPROP/GABC123..."`}
          />
          <CodeExample
            title="Fetch Legal Passport — Python"
            language="python"
            code={`import httpx

resp = httpx.get(
    "${base}/passport/stellar-testnet/MRPROP/GABC123...",
    headers={"X-API-Key": "<your-api-key>"},
)
passport = resp.json()`}
          />
          <CodeExample
            title="Fetch Legal Passport — JavaScript"
            language="js"
            code={`const resp = await fetch(
  "${base}/passport/stellar-testnet/MRPROP/GABC123...",
  { headers: { "X-API-Key": "<your-api-key>" } }
);
const passport = await resp.json();`}
          />
          <CodeExample
            title="Approve an Extraction — curl"
            language="bash"
            code={`curl -X POST \\
  -H "Authorization: Bearer <your-reviewer-token>" \\
  -H "Content-Type: application/json" \\
  -d '{"disclaimer": "INTERNAL REVIEW ONLY - NOT LEGAL ADVICE"}' \\
  "${base}/extractions/<extraction_id>/approvals"`}
          />
        </div>
      </Section>

      {/* Response codes */}
      <Section title="HTTP Status Codes">
        <div className="overflow-hidden rounded-lg ring-1 ring-slate-200">
          <table className="min-w-full divide-y divide-slate-200">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Code</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Meaning</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white text-sm">
              {[
                ['200', 'Success'],
                ['201', 'Created'],
                ['401', 'Invalid or missing credentials'],
                ['403', 'Forbidden — missing Authorization header or insufficient role'],
                ['404', 'Resource not found'],
                ['409', 'Conflict — e.g. extraction already approved'],
                ['422', 'Validation error — check request body'],
                ['429', 'Rate limit exceeded (60 req/min per API key)'],
                ['503', 'Stellar Horizon network error'],
              ].map(([code, meaning]) => (
                <tr key={code}>
                  <td className="px-4 py-3 font-mono font-semibold text-slate-900">{code}</td>
                  <td className="px-4 py-3 text-slate-700">{meaning}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-4 text-lg font-semibold text-slate-900">{title}</h3>
      {children}
    </div>
  )
}

function AuthCard({ name, header, description, configuredIn }: {
  name: string; header: string; description: string; configuredIn: string
}) {
  return (
    <div className="rounded-lg bg-white p-4 ring-1 ring-slate-200">
      <p className="font-medium text-slate-900">{name}</p>
      <code className="mt-1 block font-mono text-xs text-indigo-700">{header}</code>
      <p className="mt-2 text-sm text-slate-600">{description}</p>
      <p className="mt-2 text-xs text-slate-400">
        Configured in the backend <span className="font-mono">.env</span> file: <span className="font-mono">{configuredIn}</span>
      </p>
    </div>
  )
}

function EndpointTable({ endpoints }: {
  endpoints: { method: string; path: string; auth: string; description: string }[]
}) {
  const METHOD_COLOR: Record<string, string> = {
    GET: 'bg-blue-50 text-blue-700',
    POST: 'bg-green-50 text-green-700',
    PATCH: 'bg-amber-50 text-amber-700',
    DELETE: 'bg-red-50 text-red-700',
  }
  const AUTH_COLOR: Record<string, string> = {
    none: 'text-slate-400',
    Bearer: 'text-indigo-600',
    'X-API-Key': 'text-emerald-600',
  }

  return (
    <div className="overflow-hidden rounded-lg ring-1 ring-slate-200">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Method</th>
            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Path</th>
            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Auth</th>
            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Description</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white text-sm">
          {endpoints.map(ep => (
            <tr key={ep.method + ep.path}>
              <td className="px-4 py-3">
                <span className={`inline-block rounded px-2 py-0.5 text-xs font-bold ${METHOD_COLOR[ep.method] ?? 'bg-slate-100 text-slate-700'}`}>
                  {ep.method}
                </span>
              </td>
              <td className="px-4 py-3 font-mono text-xs text-slate-800">{ep.path}</td>
              <td className={`px-4 py-3 text-xs font-medium ${AUTH_COLOR[ep.auth] ?? 'text-slate-500'}`}>{ep.auth}</td>
              <td className="px-4 py-3 text-slate-700">{ep.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CodeExample({ title, language, code }: { title: string; language: string; code: string }) {
  return (
    <div>
      <p className="mb-2 text-sm font-medium text-slate-700">{title}</p>
      <pre className="overflow-x-auto rounded-lg bg-slate-900 p-4 text-xs text-slate-100">
        <code data-language={language}>{code}</code>
      </pre>
    </div>
  )
}
