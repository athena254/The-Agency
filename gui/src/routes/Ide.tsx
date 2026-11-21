import { useCallback, useEffect, useRef, useState } from 'react'
import { Send, Square } from 'lucide-react'
import { api, type ChatResponse, type SessionResponse } from '../api/client'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  assessment?: ChatResponse['assessment']
  pending?: boolean
}

let counter = 0
function nextId() {
  counter += 1
  return `m${counter}`
}

export function IdePage() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const csrfRef = useRef<string | null>(null)
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    api
      .get<SessionResponse>('/api/session')
      .then((session) => {
        csrfRef.current = session.csrf_token
      })
      .catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setError(null)
    const userMessage: Message = { id: nextId(), role: 'user', content: text }
    const pending: Message = { id: nextId(), role: 'assistant', content: '', pending: true }
    setMessages((prev) => [...prev, userMessage, pending])
    setBusy(true)

    try {
      const token = csrfRef.current
      const result = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'X-Prototype-CSRF': token } : {}),
        },
        body: JSON.stringify({ message: text }),
      })
      if (!result.ok) {
        if (result.status === 403) csrfRef.current = null
        const body = await result.text()
        throw new Error(`${result.status}: ${body.slice(0, 160)}`)
      }
      const data = (await result.json()) as ChatResponse
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pending.id
            ? { ...m, content: data.response, assessment: data.assessment, pending: false }
            : m,
        ),
      )
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      setMessages((prev) => prev.filter((m) => m.id !== pending.id))
    } finally {
      setBusy(false)
    }
  }, [busy, input])

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 lg:flex-row">
      {/* Chat column */}
      <section className="mc-panel flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div>
            <h2 className="text-sm font-semibold text-text-1">Isolated chat</h2>
            <div className="mc-label mt-0.5">model-only · no tools · no memory</div>
          </div>
          <span className="mc-value text-[11px] text-text-3">mercury-3</span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center">
              <div className="text-sm text-text-2">Start a conversation</div>
              <div className="mt-1 max-w-sm text-xs text-text-3">
                This is the isolated prototype path: it runs the model without tools, memory,
                or Lattice mirroring. The governed task path arrives with peer authority.
              </div>
            </div>
          ) : (
            <ul className="space-y-4">
              {messages.map((m) => (
                <li key={m.id} className="flex gap-3">
                  <span
                    className={`mt-1 h-2 w-2 flex-none rounded-full ${
                      m.role === 'user' ? 'bg-signal' : 'bg-text-3'
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="mc-label mb-1">{m.role === 'user' ? 'you' : 'remex'}</div>
                    {m.pending ? (
                      <div className="flex items-center gap-2 text-sm text-text-3">
                        <span className="mc-status-dot bg-signal animate-pulse" />
                        thinking
                      </div>
                    ) : (
                      <div className="whitespace-pre-wrap text-sm text-text-1">{m.content}</div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div ref={endRef} />
        </div>

        <div className="border-t border-line p-4">
          {error ? <div className="mb-2 text-xs text-red">{error}</div> : null}
          <div className="flex items-end gap-2">
            <textarea
              className="mc-input min-h-[68px] flex-1 resize-y"
              placeholder="Message the Butler…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void send()
                }
              }}
            />
            <button
              type="button"
              className="mc-btn mc-btn-primary flex-none"
              onClick={() => void send()}
              disabled={busy || !input.trim()}
            >
              {busy ? <Square size={13} /> : <Send size={13} />}
              Send
            </button>
          </div>
        </div>
      </section>

      {/* Context column */}
      <aside className="mc-panel w-full flex-none overflow-y-auto p-5 lg:w-80">
        <h2 className="text-sm font-semibold text-text-1">Last assessment</h2>
        <div className="mc-label mt-0.5">honest status, not verification</div>
        <dl className="mt-4 space-y-3">
          <Row label="Execution">
            <ToneText
              value={messages.find((m) => m.assessment)?.assessment?.execution_status ?? '—'}
            />
          </Row>
          <Row label="Verification">
            <ToneText
              value={messages.find((m) => m.assessment)?.assessment?.verification ?? '—'}
            />
          </Row>
          <Row label="Risk">
            <ToneText
              value={messages.find((m) => m.assessment)?.assessment?.risk_status ?? '—'}
            />
          </Row>
          <Row label="Independently verified">
            <ToneText
              value={
                messages.find((m) => m.assessment)?.assessment?.independently_verified
                  ? 'yes'
                  : 'no'
              }
            />
          </Row>
        </dl>
        <div className="mt-5 border-t border-line pt-4 text-xs leading-relaxed text-text-3">
          A provider reply is not task success. Independent checks and durable authorization
          are separate gates.
        </div>
      </aside>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="mc-label">{label}</dt>
      <dd className="mc-value text-[11.5px] text-text-1">{children}</dd>
    </div>
  )
}

function ToneText({ value }: { value: string }) {
  const tone =
    value === 'completed' || value === 'available'
      ? 'text-green'
      : value === 'not_performed' || value === 'not_assessed' || value === 'no'
        ? 'text-amber'
        : 'text-text-2'
  return <span className={tone}>{value}</span>
}
