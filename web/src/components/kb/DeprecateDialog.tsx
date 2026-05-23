'use client'

import * as React from 'react'
import { AlertTriangle, Loader2, Trash2 } from 'lucide-react'

import { useUserStore } from '@/stores/useUserStore'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const isLocal = process.env.NEXT_PUBLIC_MODE === 'local'

type CitingPage = { id: string; path: string; filename: string; title?: string | null }

type Preview =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; citing_count: number; citing: CitingPage[] }

/** Confirmation dialog before kicking off a deprecate-with-agent run.
 *
 * Fetches the blast radius up front (`GET /v1/agents/deprecate/{id}/preview`)
 * so the user sees the impact before approving. The dialog itself never
 * mutates anything — it just gates the transition into the agent run panel.
 */
export function DeprecateDialog({
  docId, filename, onCancel, onConfirm,
}: {
  docId: string
  filename: string
  onCancel: () => void
  /** Called when the user clicks Deprecate. Parent owns the panel mount. */
  onConfirm: () => void
}) {
  const [preview, setPreview] = React.useState<Preview>({ status: 'loading' })

  React.useEffect(() => {
    const ac = new AbortController()
    const headers: Record<string, string> = {}
    if (!isLocal) {
      const token = useUserStore.getState().accessToken
      if (token) headers.Authorization = `Bearer ${token}`
    }
    fetch(`${API_URL}/v1/agents/deprecate/${docId}/preview`, { headers, signal: ac.signal })
      .then(async (res) => {
        if (!res.ok) {
          let msg = `HTTP ${res.status}`
          try {
            const body = await res.json()
            if (typeof body?.detail === 'string') msg = body.detail
          } catch { /* non-JSON */ }
          setPreview({ status: 'error', message: msg })
          return
        }
        const body = await res.json()
        setPreview({
          status: 'ready',
          citing_count: body.citing_count ?? 0,
          citing: body.citing ?? [],
        })
      })
      .catch((err) => {
        if ((err as Error).name === 'AbortError') return
        setPreview({ status: 'error', message: (err as Error).message })
      })
    return () => ac.abort()
  }, [docId])

  const fastPath = preview.status === 'ready' && preview.citing_count === 0
  const canConfirm = preview.status === 'ready'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="bg-background border border-border rounded-lg p-6 w-[28rem] max-w-[90vw] shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3 mb-4">
          <div className="size-8 rounded-full bg-destructive/10 flex items-center justify-center shrink-0">
            <AlertTriangle className="size-4 text-destructive" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold leading-tight">Deprecate source?</h3>
            <p className="text-xs text-muted-foreground mt-0.5 truncate" title={filename}>{filename}</p>
          </div>
        </div>

        <div className="text-sm text-foreground space-y-2 mb-4">
          {preview.status === 'loading' && (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              <span>Computing blast radius...</span>
            </div>
          )}

          {preview.status === 'error' && (
            <div className="text-destructive text-xs">
              Could not load impact: {preview.message}
            </div>
          )}

          {preview.status === 'ready' && fastPath && (
            <p>
              No wiki pages cite this source. It will be deleted directly —
              no agent run is needed.
            </p>
          )}

          {preview.status === 'ready' && !fastPath && (
            <>
              <p>
                The agent will update {preview.citing_count} wiki page{preview.citing_count === 1 ? '' : 's'} that cite{preview.citing_count === 1 ? 's' : ''} this source,
                then the source itself will be deleted.
              </p>
              <ul className="text-xs text-muted-foreground space-y-0.5 max-h-40 overflow-y-auto rounded border border-border/50 bg-muted/30 p-2">
                {preview.citing.map((c) => (
                  <li key={c.id}>
                    <code className="text-[11px]">{c.path}{c.filename}</code>
                    {c.title ? <span className="ml-1">— {c.title}</span> : null}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">
                For each page, the agent will rewrite to remove the citation, or archive
                the page if it existed only because of this source.
              </p>
            </>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={!canConfirm}
            className="inline-flex items-center gap-1.5 rounded-lg bg-destructive px-4 py-1.5 text-sm font-medium text-destructive-foreground hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            <Trash2 className="size-3.5" />
            {fastPath ? 'Delete source' : 'Deprecate with agent'}
          </button>
        </div>
      </div>
    </div>
  )
}
