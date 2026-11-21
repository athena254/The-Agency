import { useState, type ReactNode } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  FileSignature,
  KeyRound,
  RefreshCw,
  ShieldCheck,
  Vote,
} from 'lucide-react'
import {
  fetchProposals,
  GovError,
  parseEnvelope,
  submitBallot,
  type BallotVerdict,
  type GovernanceProposal,
  type GovernanceVote,
} from '../api/governance'
import { useAuth } from '../stores/auth'
import { PageHeader as Header } from '../components/ui'
import { Chip, LoadingBlock, PanelRow, StateChip } from './PilotShared'

/**
  Lattice governance panel (Slice D).

  Shows the open proposals the backend exposes (affected parties, status,
  evidence) and lets the OWNER cast a real ballot. The panel never signs:
  the human signs the envelope OUTSIDE the browser with
  `agency consensus-sign-ballot` (D-06, separate-device attestation), pastes
  the signed bytes, and the backend's PeerAuthority is the only verifier.
  No synthetic, default, or demo ballots exist anywhere in this flow.
*/

const SIGN_HELPER =
  'PYTHONPATH=src .venv/Scripts/python.exe -c "from agency.cli import main; main()"'
const GENESIS_DEFAULT = 'data/consensus/genesis.json'
const BALLOT_DECISIONS = ['approve', 'deny', 'abstain'] as const

function decisionTone(decision: string): 'green' | 'red' | 'muted' | 'amber' {
  switch (decision.toLowerCase()) {
    case 'approve':
    case 'passed':
      return 'green'
    case 'deny':
    case 'denied':
      return 'red'
    case 'abstain':
      return 'amber'
    default:
      return 'muted'
  }
}

function CommandBlock({ children }: { children: ReactNode }) {
  return (
    <pre className="mc-value mt-2 overflow-x-auto rounded-control border border-line bg-ink-1 px-3 py-2 text-[10.5px] leading-relaxed text-text-2">
      {children}
    </pre>
  )
}

function GrantGate() {
  const govGrant = useAuth((s) => s.govGrant)
  const setGovGrant = useAuth((s) => s.setGovGrant)
  const [draft, setDraft] = useState('')

  return (
    <section className="mc-panel px-5 py-4">
      <div className="flex items-center gap-2">
        <KeyRound size={14} className="text-text-3" />
        <div className="mc-label">Owner read grant</div>
        <span className="ml-auto">
          {govGrant ? <Chip tone="green">grant set for session</Chip> : <Chip tone="amber">no grant</Chip>}
        </span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-text-3">
        Proposal data is grant-gated: every read must carry a signed, task-scoped grant bound to
        the exact route. Mint it outside this browser — the GUI holds no keys — then paste the{' '}
        <span className="mc-value">grant_header</span> value. It stays in memory for this session
        only. The pinned genesis must be the one the API server loaded (
        <span className="mc-value">AGENCY_GENESIS_PATH</span>, default{' '}
        <span className="mc-value">{GENESIS_DEFAULT}</span>).
      </p>
      <CommandBlock>
        {SIGN_HELPER} consensus-sign-grant \{'\n'}
        {'  '}--genesis {GENESIS_DEFAULT} \{'\n'}
        {'  '}--key &lt;owner-private-key-base64&gt; \{'\n'}
        {'  '}--task-id &quot;GET /v1/governance/proposals&quot; \{'\n'}
        {'  '}--proposal-id &lt;proposal-id&gt; --json
      </CommandBlock>
      <div className="mt-3 flex items-end gap-2">
        <div className="flex-1">
          <label htmlFor="mc-gov-grant" className="sr-only">
            Signed X-Agency-Grant header value
          </label>
          <textarea
            id="mc-gov-grant"
            className="mc-textarea"
            rows={3}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="paste the minted grant_header value (base64)"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <button
          type="button"
          className="mc-btn mc-btn-primary flex-none"
          onClick={() => {
            setGovGrant(draft.trim() || null)
          }}
        >
          Use grant
        </button>
        {govGrant ? (
          <button
            type="button"
            className="mc-btn mc-btn-danger flex-none"
            onClick={() => {
              setGovGrant(null)
              setDraft('')
            }}
          >
            Clear
          </button>
        ) : null}
      </div>
    </section>
  )
}

function VoteRow({ vote }: { vote: GovernanceVote }) {
  return (
    <li className="px-1 py-2">
      <div className="flex items-center gap-2">
        <span className="mc-value truncate text-[11px] text-text-2">{vote.voter_id}</span>
        <Chip tone={decisionTone(vote.decision)}>{vote.decision}</Chip>
        <span className="mc-value ml-auto flex-none text-[10.5px] text-text-3">
          weight {vote.weight}
        </span>
      </div>
      {vote.evidence.length > 0 ? (
        <ul className="mt-1 space-y-0.5">
          {vote.evidence.map((item) => (
            <li key={item} className="text-xs leading-relaxed text-text-2">
              · {item}
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-0.5 text-[10.5px] text-text-3">no evidence cited</div>
      )}
      <div className="mc-value mt-0.5 text-[10.5px] text-text-3">{vote.timestamp}</div>
    </li>
  )
}

function BallotComposer({ proposal }: { proposal: GovernanceProposal }) {
  const [decision, setDecision] = useState<(typeof BALLOT_DECISIONS)[number]>('approve')
  const [seq, setSeq] = useState(1)
  const [pasted, setPasted] = useState('')
  const [verdict, setVerdict] = useState<BallotVerdict | null>(null)

  const submit = useMutation({
    mutationFn: submitBallot,
    onSuccess: (result) => setVerdict(result),
    onError: () => setVerdict(null),
  })

  const envelope = parseEnvelope(pasted)
  const pasteError = pasted.trim().length > 0 && envelope === null

  return (
    <details className="mt-3 border-t border-line pt-3">
      <summary className="flex cursor-pointer items-center gap-2 text-xs text-text-2">
        <FileSignature size={13} className="text-text-3" /> Cast a ballot on this proposal
      </summary>

      <p className="mt-2 text-xs leading-relaxed text-text-3">
        Your vote must be a real Ed25519 ballot signed by you, outside this browser — the GUI never
        holds keys and never fabricates votes. Run the signing command on the machine that holds
        your owner key, then paste the signed output below. The backend verifies the envelope and
        its verdict is shown as-is.
      </p>

      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`mc-ballot-decision-${proposal.proposal_id}`} className="mc-label">
            Decision to sign
          </label>
          <select
            id={`mc-ballot-decision-${proposal.proposal_id}`}
            className="mc-input mt-1"
            value={decision}
            onChange={(e) => setDecision(e.target.value as (typeof BALLOT_DECISIONS)[number])}
          >
            {BALLOT_DECISIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`mc-ballot-seq-${proposal.proposal_id}`} className="mc-label">
            Monotonic seq
          </label>
          <input
            id={`mc-ballot-seq-${proposal.proposal_id}`}
            className="mc-input mt-1"
            type="number"
            min={1}
            value={seq}
            onChange={(e) => setSeq(Math.max(1, Number(e.target.value) || 1))}
          />
        </div>
      </div>

      <div className="mc-label mt-3">Payload to sign — run exactly this, with your key</div>
      <CommandBlock>
        {SIGN_HELPER} consensus-sign-ballot \{'\n'}
        {'  '}--genesis {GENESIS_DEFAULT} \{'\n'}
        {'  '}--key &lt;owner-private-key-base64&gt; \{'\n'}
        {'  '}--proposal-id {proposal.proposal_id} \{'\n'}
        {'  '}--decision {decision} --seq {seq} --json
      </CommandBlock>
      <p className="mt-1 text-[10.5px] text-text-3">
        The envelope binds your peer identity, the proposal id, this decision and seq under the
        pinned membership epoch — change any of them and the backend rejects the ballot.
      </p>

      <div className="mt-3">
        <label htmlFor={`mc-ballot-envelope-${proposal.proposal_id}`} className="mc-label">
          Paste the signed envelope (wire_hex from the --json output)
        </label>
        <textarea
          id={`mc-ballot-envelope-${proposal.proposal_id}`}
          className="mc-textarea mt-1"
          rows={4}
          value={pasted}
          onChange={(e) => setPasted(e.target.value)}
          placeholder='{"wire_hex": "…"} — or the raw hex / base64 line'
          autoComplete="off"
          spellCheck={false}
        />
        {pasteError ? (
          <p className="mt-1 text-xs text-amber" role="alert">
            Could not read an envelope from that paste.
          </p>
        ) : null}
      </div>

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          className="mc-btn mc-btn-primary"
          disabled={!envelope || submit.isPending}
          onClick={() => {
            if (envelope) submit.mutate(envelope)
          }}
        >
          <Vote size={13} /> Submit ballot for verification
        </button>
        {submit.isPending ? <Chip tone="muted">verifying…</Chip> : null}
      </div>

      <div aria-live="polite">
        {submit.error ? (
          <div
            role="alert"
            className="mt-3 flex items-start gap-2 rounded-control border border-red/30 bg-red/10 px-3 py-2 text-xs text-red"
          >
            <ShieldCheck size={13} className="mt-0.5 flex-none" />
            <span>
              Backend rejected the ballot:{' '}
              {submit.error instanceof GovError ? submit.error.detail : String(submit.error)}
            </span>
          </div>
        ) : null}
        {verdict ? <VerdictView verdict={verdict} /> : null}
      </div>
    </details>
  )
}

function VerdictView({ verdict }: { verdict: BallotVerdict }) {
  const tally = verdict.tally as unknown as Record<string, number | undefined>
  const decisionCount = tally[verdict.decision] ?? 0
  const accepted = decisionCount > 0 || verdict.outcome === 'passed'
  return (
    <div
      className={`mt-3 rounded-control border px-3 py-3 ${
        accepted ? 'border-green/30 bg-green/10' : 'border-amber/30 bg-amber/10'
      }`}
    >
      <div className="flex items-center gap-2">
        <Chip tone={accepted ? 'green' : 'amber'}>{accepted ? 'verified' : 'recorded'}</Chip>
        <span className="text-xs text-text-2">
          signed by <span className="mc-value">{verdict.voter_peer_id}</span> ·{' '}
          <Chip tone={decisionTone(verdict.decision)}>{verdict.decision}</Chip> · seq{' '}
          {verdict.ballot_seq}
        </span>
      </div>
      <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
        <PanelRow
          label="Tally (approve/deny/abstain)"
        >
          {`${verdict.tally.approve}/${verdict.tally.deny}/${verdict.tally.abstain}`}
        </PanelRow>
        <PanelRow label="Quorum">{`${verdict.quorum_required} validators · ${verdict.quorum_met ? 'met' : 'not met'}`}</PanelRow>
        <PanelRow label="Outcome">
          <Chip tone={decisionTone(verdict.outcome)}>{verdict.outcome}</Chip>
        </PanelRow>
      </dl>
    </div>
  )
}

function ProposalCard({ proposal }: { proposal: GovernanceProposal }) {
  return (
    <li className="mc-panel px-5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mc-value text-[11px] text-text-1">{proposal.proposal_id}</span>
        <Chip tone="signal">{proposal.proposal_type}</Chip>
        <StateChip state={proposal.status} />
        <span className="mc-value ml-auto flex-none text-[10.5px] text-text-3">
          proposer {proposal.proposer_id}
        </span>
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        <PanelRow label="Quorum required">{`${Math.round(proposal.quorum_required * 100)}%`}</PanelRow>
        <PanelRow label="Votes recorded">{proposal.vote_count}</PanelRow>
        <PanelRow label="Expires at">{proposal.expires_at ?? '—'}</PanelRow>
        <PanelRow label="Eligible voters">
          {proposal.eligible_voters.length > 0 ? `${proposal.eligible_voters.length} parties` : 'none recorded'}
        </PanelRow>
      </dl>
      {proposal.eligible_voters.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {proposal.eligible_voters.map((voter) => (
            <Chip key={voter} tone="muted">
              {voter}
            </Chip>
          ))}
        </div>
      ) : null}
      {proposal.votes.length > 0 ? (
        <div className="mt-3 border-t border-line pt-2">
          <div className="mc-label mb-1">Recorded votes and cited evidence</div>
          <ul className="divide-y divide-line">
            {proposal.votes.map((vote) => (
              <VoteRow key={`${vote.voter_id}-${vote.timestamp}`} vote={vote} />
            ))}
          </ul>
        </div>
      ) : (
        <div className="mt-3 text-[10.5px] text-text-3">
          No votes recorded on the lattice yet. Peer ballots verified by the authority appear in
          the verdict panel below once cast.
        </div>
      )}
      <BallotComposer proposal={proposal} />
    </li>
  )
}

function ProposalsPanel({ grant }: { grant: string }) {
  const proposalsQuery = useQuery({
    queryKey: ['gov-proposals', grant],
    queryFn: () => fetchProposals(grant),
    refetchInterval: 15_000,
  })

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-text-1">Open proposals</h3>
        <div className="flex items-center gap-2">
          <span className="mc-label">GET /v1/governance/proposals</span>
          {proposalsQuery.data ? (
            <Chip tone="green">{proposalsQuery.data.proposals.length} open</Chip>
          ) : null}
          <button
            type="button"
            className="mc-icon-btn"
            onClick={() => void proposalsQuery.refetch()}
            disabled={proposalsQuery.isFetching}
            aria-label="Refresh proposals"
            title="Refresh proposals"
          >
            <RefreshCw
              size={13}
              className={proposalsQuery.isFetching ? 'animate-spin' : ''}
              aria-hidden="true"
            />
          </button>
        </div>
      </div>

      {proposalsQuery.isLoading ? (
        <div className="mc-panel" aria-live="polite">
          <LoadingBlock label="Reading proposals with your grant…" />
        </div>
      ) : proposalsQuery.error ? (
        <div className="mc-panel p-4" role="alert">
          <p className="text-xs leading-relaxed text-text-2">
            The proposals read was refused — nothing was fetched or shown.
          </p>
          <p className="mt-2 text-xs text-red">
            {proposalsQuery.error instanceof GovError
              ? proposalsQuery.error.detail
              : String(proposalsQuery.error)}
          </p>
          <p className="mt-2 text-[10.5px] text-text-3">
            Re-mint the grant with <span className="mc-value">consensus-sign-grant</span> using the
            genesis the server runs with, then paste it above.
          </p>
        </div>
      ) : proposalsQuery.data && proposalsQuery.data.proposals.length > 0 ? (
        <ul className="space-y-3">
          {proposalsQuery.data.proposals.map((proposal) => (
            <ProposalCard key={proposal.proposal_id} proposal={proposal} />
          ))}
        </ul>
      ) : (
        <div className="mc-panel px-5 py-6 text-center text-xs text-text-3" role="status">
          No open proposals. Proposals appear here once submitted on the lattice (
          <span className="mc-value">POST /v1/governance/propose</span> stays grant+finality gated
          to writes by the owner).
        </div>
      )}
    </section>
  )
}

export function LatticePage() {
  const govGrant = useAuth((s) => s.govGrant)

  return (
    <div className="space-y-4">
      <Header
        title="Lattice"
        detail="Governance: open proposals, affected parties, evidence, and real human ballots."
      />

      <div className="mc-panel flex items-start gap-3 px-5 py-4">
        <ShieldCheck size={15} className="mt-0.5 flex-none text-green" />
        <div className="min-w-0 text-xs leading-relaxed text-text-3">
          <span className="text-text-1">Real ballots only.</span> This panel is a transport and a
          verdict display: it shows the exact payload and signing command, accepts the envelope
          you signed on your own machine, and submits it to the backend for Ed25519 verification.
          It never signs, never defaults a decision, and holds no key material — every ballot is
          explicit human attestation (decision D-06).
        </div>
      </div>

      <GrantGate />
      {govGrant ? <ProposalsPanel grant={govGrant} /> : null}
    </div>
  )
}
