import { useEffect, useMemo, useState } from 'react'

const riskOrder = ['critical', 'high', 'medium', 'low', 'unknown']
const severityOrder = ['critical', 'high', 'medium', 'low', 'info', 'unknown']

const riskTone = {
  critical: 'text-rose-300 border-rose-300/40 bg-rose-500/15',
  high: 'text-orange-300 border-orange-300/40 bg-orange-500/15',
  medium: 'text-amber-300 border-amber-300/40 bg-amber-500/15',
  low: 'text-emerald-300 border-emerald-300/40 bg-emerald-500/15',
  unknown: 'text-slate-300 border-slate-300/30 bg-slate-500/10',
}

function normalizeRisk(v) {
  const s = String(v || 'unknown').trim().toLowerCase()
  if (['critical', 'high', 'medium', 'low'].includes(s)) return s
  return 'unknown'
}

function statusOf(item) {
  if (item.error) return 'error'
  if (item.skill_md_found === false) return 'missing_skill_md'
  return 'audited'
}

function asArray(value) {
  return Array.isArray(value) ? value : []
}

function StatCard({ label, value }) {
  return (
    <div className="rounded-2xl border border-cyan-200/20 bg-cyan-950/25 p-4 shadow-xl shadow-black/20">
      <p className="font-mono text-xs uppercase tracking-widest text-cyan-200/70">{label}</p>
      <p className="mt-1 text-3xl font-bold">{value}</p>
    </div>
  )
}

function BarChart({ title, counts, order }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1
  return (
    <section className="rounded-2xl border border-sky-200/20 bg-sky-950/25 p-5">
      <h3 className="mb-4 text-sm font-semibold tracking-wide text-sky-100">{title}</h3>
      <div className="space-y-3">
        {order.map((key) => {
          const value = counts[key] || 0
          const pct = Math.round((value / total) * 100)
          return (
            <div key={key} className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className={`capitalize ${riskTone[normalizeRisk(key)].split(' ')[0]}`}>{key}</span>
                <span className="text-slate-300">{value} ({pct}%)</span>
              </div>
              <div className="h-2.5 overflow-hidden rounded-full bg-slate-700/50">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${riskTone[normalizeRisk(key)].split(' ')[2]}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function SkillCard({ item }) {
  const status = statusOf(item)
  const risk = normalizeRisk(item.audit?.risk_level)
  const findings = asArray(item.audit?.findings)
  const summary =
    item.error || item.audit?.summary || (item.skill_md_found === false ? 'SKILL.md missing in zip.' : 'No summary available.')

  return (
    <article className="rounded-2xl border border-sky-100/15 bg-slate-900/40 p-4 shadow-lg shadow-black/30">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-lg font-semibold">{item.slug || 'unknown-skill'}</h4>
          <p className="font-mono text-xs text-slate-400">{item.zip_path || 'No zip path'}</p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <span className={`rounded-full border px-2 py-1 font-mono ${riskTone[risk]}`}>risk:{risk}</span>
          <span className="rounded-full border border-slate-200/25 bg-slate-800/50 px-2 py-1 font-mono text-slate-200">
            status:{status}
          </span>
          {typeof item.audit?.dangerous === 'boolean' && (
            <span className="rounded-full border border-fuchsia-200/30 bg-fuchsia-600/20 px-2 py-1 font-mono text-fuchsia-200">
              dangerous:{String(item.audit.dangerous)}
            </span>
          )}
        </div>
      </div>

      <p className="mt-3 text-sm text-slate-200/90">{summary}</p>

      <details className="mt-3 border-t border-slate-200/10 pt-3">
        <summary className="cursor-pointer text-sm text-cyan-200">Findings ({findings.length})</summary>
        {findings.length === 0 ? (
          <p className="mt-2 text-sm text-slate-400">No findings.</p>
        ) : (
          <ul className="mt-2 space-y-2 text-sm">
            {findings.map((f, idx) => {
              const sev = normalizeRisk(f.severity)
              return (
                <li key={`${item.slug}-finding-${idx}`} className="rounded-xl border border-slate-200/10 bg-slate-800/40 p-3">
                  <p className="font-medium">
                    <span className={`mr-2 font-mono text-xs uppercase ${riskTone[sev].split(' ')[0]}`}>[{sev}]</span>
                    {f.title || 'Untitled finding'}
                  </p>
                  <p className="mt-1 text-slate-300">{f.why || 'No rationale provided.'}</p>
                </li>
              )
            })}
          </ul>
        )}
      </details>
    </article>
  )
}

export default function App() {
  const [items, setItems] = useState([])
  const [source, setSource] = useState('Loading skill_audit_report.json...')
  const [search, setSearch] = useState('')
  const [riskFilter, setRiskFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')

  useEffect(() => {
    fetch('/skill_audit_report.json', { cache: 'no-store' })
      .then((res) => {
        if (!res.ok) throw new Error('fetch failed')
        return res.json()
      })
      .then((data) => {
        if (!Array.isArray(data)) throw new Error('Invalid JSON shape')
        setItems(data)
        setSource(`Loaded default dataset (${data.length} entries)`)
      })
      .catch(() => {
        setItems([])
        setSource('Auto-load failed. Upload a JSON file.')
      })
  }, [])

  const filtered = useMemo(() => {
    return items.filter((item) => {
      const slug = String(item.slug || '').toLowerCase()
      const risk = normalizeRisk(item.audit?.risk_level)
      const status = statusOf(item)
      const q = search.trim().toLowerCase()

      return (
        (!q || slug.includes(q)) &&
        (riskFilter === 'all' || risk === riskFilter) &&
        (statusFilter === 'all' || status === statusFilter)
      )
    })
  }, [items, search, riskFilter, statusFilter])

  const stats = useMemo(() => {
    const findings = filtered.reduce((sum, item) => sum + asArray(item.audit?.findings).length, 0)
    return {
      total: filtered.length,
      audited: filtered.filter((x) => statusOf(x) === 'audited').length,
      errors: filtered.filter((x) => statusOf(x) === 'error').length,
      dangerous: filtered.filter((x) => x.audit?.dangerous === true).length,
      findings,
    }
  }, [filtered])

  const riskCounts = useMemo(() => {
    const out = Object.fromEntries(riskOrder.map((k) => [k, 0]))
    for (const item of filtered) out[normalizeRisk(item.audit?.risk_level)] += 1
    return out
  }, [filtered])

  const severityCounts = useMemo(() => {
    const out = Object.fromEntries(severityOrder.map((k) => [k, 0]))
    for (const item of filtered) {
      for (const finding of asArray(item.audit?.findings)) {
        out[normalizeRisk(finding.severity)] += 1
      }
    }
    return out
  }, [filtered])

  const onFile = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    const text = await file.text()
    try {
      const data = JSON.parse(text)
      if (!Array.isArray(data)) throw new Error('shape')
      setItems(data)
      setSource(`Loaded ${file.name} (${data.length} entries)`)
    } catch {
      setSource('Invalid JSON file. Expected top-level array.')
    }
  }

  return (
    <div className="relative min-h-screen overflow-x-hidden px-4 py-8 sm:px-6 lg:px-10">
      <div className="pointer-events-none absolute -left-20 top-10 h-72 w-72 animate-floatSlow rounded-full bg-cyan-400/20 blur-3xl" />
      <div className="pointer-events-none absolute -right-16 bottom-8 h-72 w-72 animate-floatSlow rounded-full bg-orange-400/20 blur-3xl" />

      <main className="relative mx-auto grid w-full max-w-7xl gap-4">
        <header className="rounded-3xl border border-cyan-200/20 bg-slate-900/70 p-6 shadow-2xl shadow-black/30 backdrop-blur">
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-cyan-300">Clawdit</p>
          <h1 className="mt-2 text-3xl font-bold leading-tight sm:text-5xl">Skill Audit Atlas</h1>
          <p className="mt-3 max-w-3xl text-slate-300">
            Immersive security map for OpenClaw skills with risk posture, failure tracking, and finding-level drilldowns.
          </p>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <span className="rounded-full border border-cyan-300/30 bg-cyan-500/10 px-3 py-1 font-mono text-xs text-cyan-200">
              {source}
            </span>
            <label className="cursor-pointer rounded-full border border-slate-200/25 bg-slate-800/70 px-3 py-1 font-mono text-xs text-slate-200">
              Upload JSON
              <input type="file" className="hidden" accept="application/json" onChange={onFile} />
            </label>
          </div>
        </header>

        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <StatCard label="Skills" value={stats.total} />
          <StatCard label="Audited" value={stats.audited} />
          <StatCard label="Failures" value={stats.errors} />
          <StatCard label="Dangerous" value={stats.dangerous} />
          <StatCard label="Findings" value={stats.findings} />
        </section>

        <section className="grid gap-3 rounded-3xl border border-slate-100/15 bg-slate-900/60 p-4 sm:grid-cols-3">
          <div className="sm:col-span-1">
            <label className="font-mono text-xs uppercase tracking-widest text-slate-300">Search skill</label>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="e.g. clawflight"
              className="mt-1 w-full rounded-xl border border-slate-300/20 bg-slate-950/70 px-3 py-2 text-sm outline-none ring-cyan-300/50 focus:ring"
            />
          </div>
          <div>
            <label className="font-mono text-xs uppercase tracking-widest text-slate-300">Risk filter</label>
            <select
              value={riskFilter}
              onChange={(e) => setRiskFilter(e.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-300/20 bg-slate-950/70 px-3 py-2 text-sm"
            >
              <option value="all">All risks</option>
              {riskOrder.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="font-mono text-xs uppercase tracking-widest text-slate-300">Status filter</label>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-300/20 bg-slate-950/70 px-3 py-2 text-sm"
            >
              <option value="all">All statuses</option>
              <option value="audited">Audited</option>
              <option value="error">Download/Audit error</option>
              <option value="missing_skill_md">Missing SKILL.md</option>
            </select>
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <BarChart title="Risk Distribution" counts={riskCounts} order={riskOrder} />
          <BarChart title="Finding Severity Mix" counts={severityCounts} order={severityOrder} />
        </section>

        <section className="space-y-3 rounded-3xl border border-slate-100/15 bg-slate-900/60 p-4">
          <h2 className="text-lg font-semibold">Skill Drilldown</h2>
          {filtered.length === 0 ? (
            <p className="text-slate-400">No skills match the current filters.</p>
          ) : (
            <div className="grid gap-3">
              {filtered.map((item, idx) => (
                <SkillCard item={item} key={`${item.slug || 'skill'}-${idx}`} />
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
