'use client'

import * as React from 'react'
import { useRouter } from 'next/navigation'
import { useKBStore } from '@/stores'
import { Loader2 } from 'lucide-react'

const isLocal = process.env.NEXT_PUBLIC_MODE === 'local'

export default function NewKnowledgeBasePage() {
  const [name, setName] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState('')
  const router = useRouter()
  const createKB = useKBStore((s) => s.createKB)

  if (isLocal) {
    return (
      <div className="max-w-md mx-auto p-8">
        <h1 className="text-xl font-semibold tracking-tight">Local Workspace Wiki</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Local mode supports one wiki per workspace folder. To create another wiki,
          start LLM Wiki with a different folder or add another MCP server scoped to
          that folder.
        </p>
        <button
          type="button"
          onClick={() => router.push('/wikis')}
          className="mt-6 w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors cursor-pointer"
        >
          Back to wiki
        </button>
      </div>
    )
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return

    setLoading(true)
    setError('')

    try {
      const kb = await createKB(trimmed, description.trim() || undefined)
      router.push(`/wikis/${kb.slug}`)
    } catch (err) {
      setError((err as Error).message || 'Failed to create wiki')
      setLoading(false)
    }
  }

  return (
    <div className="max-w-md mx-auto p-8">
      <h1 className="text-xl font-semibold tracking-tight">Create Wiki</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        A wiki is an LLM-maintained knowledge base compiled from your raw sources.
      </p>

      <form onSubmit={handleCreate} className="mt-6 space-y-4">
        <div>
          <label htmlFor="kb-name" className="block text-sm font-medium mb-1.5">
            Name
          </label>
          <input
            id="kb-name"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
            placeholder="My Research"
            required
          />
        </div>

        <div>
          <label htmlFor="kb-description" className="block text-sm font-medium mb-1.5">
            Description <span className="text-muted-foreground font-normal">(optional)</span>
          </label>
          <textarea
            id="kb-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background resize-none"
            placeholder="Notes and papers about..."
          />
        </div>

        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}

        <button
          type="submit"
          disabled={loading || !name.trim()}
          className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {loading ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              Creating...
            </>
          ) : (
            'Create'
          )}
        </button>
      </form>
    </div>
  )
}
