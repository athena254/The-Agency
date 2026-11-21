/*
  Governance /v1 surface calls — proposals listing and signed ballot submission.

  Reads are grant-gated: GET /v1/governance/proposals requires the
  X-Agency-Grant header (a signed, task-scoped grant the OWNER mints outside
  the browser with `agency consensus-sign-grant`; see docs/SLICE_D_GUI_BALLOTS.md).
  The GUI only carries the minted grant bytes in memory and never holds key
  material.

  Ballot submission carries no grant: POST /v1/governance/ballot is authorized
  exclusively by the Ed25519 signature inside the pasted envelope, produced by
  the human via `agency consensus-sign-ballot`. Nothing here fabricates,
  signs, or alters a ballot — the panel is a transport and verdict display.

  Failures surface as GovError with the HTTP status and the backend `detail`.
*/

export class GovError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'GovError'
    this.status = status
    this.detail = detail
  }
}

function detailFrom(status: number, body: string): string {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    if (typeof parsed.detail === 'string') return parsed.detail
  } catch {
    /* body was not JSON */
  }
  return `HTTP ${status}: ${body.slice(0, 200) || 'no response body'}`
}

async function govRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  const text = await res.text()
  if (!res.ok) throw new GovError(res.status, detailFrom(res.status, text))
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    throw new GovError(res.status, `Unexpected non-JSON response: ${text.slice(0, 200)}`)
  }
}

/* ------------------------------- types ---------------------------------- */

export interface GovernanceVote {
  voter_id: string
  decision: string
  evidence: string[]
  weight: number
  timestamp: string
}

export interface GovernanceProposal {
  proposal_id: string
  proposal_type: string
  proposer_id: string
  quorum_required: number
  status: string
  vote_count: number
  eligible_voters: string[]
  votes: GovernanceVote[]
  expires_at: string | null
}

export interface ProposalsResponse {
  proposals: GovernanceProposal[]
}

export interface BallotTally {
  approve: number
  deny: number
  abstain: number
}

export interface BallotVerdict {
  proposal_id: string
  voter_peer_id: string
  decision: string
  ballot_seq: number
  tally: BallotTally
  quorum_required: number
  quorum_met: boolean
  outcome: string
}

export interface BallotEnvelope {
  wire_hex?: string
  wire_base64?: string
}

/* ------------------------------ requests -------------------------------- */

/** Fetch open proposals. `grant` is the base64 X-Agency-Grant header value. */
export function fetchProposals(grant: string): Promise<ProposalsResponse> {
  return govRequest<ProposalsResponse>('/v1/governance/proposals', {
    headers: { 'X-Agency-Grant': grant },
  })
}

/** Submit a pasted, human-signed ballot envelope for backend verification. */
export function submitBallot(envelope: BallotEnvelope): Promise<BallotVerdict> {
  return govRequest<BallotVerdict>('/v1/governance/ballot', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(envelope),
  })
}

/* --------------------------- paste handling ------------------------------ */

const HEX_RE = /^[0-9a-fA-F\s]+$/

/**
  Parse the owner's pasted signing output. Accepts:
  - the `consensus-sign-ballot --json` blob: {"wire_hex": "..."}
  - raw wire hex (tolerates Rich's line wrapping / stray whitespace)
  - raw wire base64
  Returns null when nothing usable was pasted. Never mutates the envelope:
  the bytes are submitted exactly as the human's signer produced them.
*/
export function parseEnvelope(text: string): BallotEnvelope | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  if (trimmed.startsWith('{')) {
    try {
      const parsed = JSON.parse(trimmed) as Record<string, unknown>
      if (typeof parsed.wire_hex === 'string' && parsed.wire_hex) return { wire_hex: parsed.wire_hex }
      if (typeof parsed.wire_base64 === 'string' && parsed.wire_base64) {
        return { wire_base64: parsed.wire_base64 }
      }
      return null
    } catch {
      return null
    }
  }
  const squashed = trimmed.replace(/\s+/g, '')
  if (HEX_RE.test(squashed) && squashed.length % 2 === 0) return { wire_hex: squashed }
  if (squashed.length > 0) return { wire_base64: squashed }
  return null
}
