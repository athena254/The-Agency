/*
  Tone maps for the Mission Control console — the single source every page
  uses to turn domain states (tasks, agents, skills, severities, audit
  results) into visual tones. Pair dots with a text label; never let colour
  carry meaning alone.
*/

export type Tone = 'neutral' | 'signal' | 'muted' | 'green' | 'amber' | 'red' | 'purple'

/** Border/background/text classes for badge-style chips. */
export const BADGE_TONES: Record<Tone, string> = {
  neutral: 'border-line bg-ink-1 text-text-2',
  muted: 'border-line bg-ink-1 text-text-3',
  signal: 'border-signal/30 bg-signal-dim text-signal-strong',
  green: 'border-green/30 bg-green/10 text-green',
  amber: 'border-amber/30 bg-amber/10 text-amber',
  red: 'border-red/30 bg-red/10 text-red',
  purple: 'border-purple/30 bg-purple/10 text-purple',
}

/** Background classes for status dots. */
export const DOT_TONES: Record<Tone, string> = {
  neutral: 'bg-text-3',
  muted: 'bg-text-3',
  signal: 'bg-signal',
  green: 'bg-green',
  amber: 'bg-amber',
  red: 'bg-red',
  purple: 'bg-purple',
}

const STATE_TONES: Record<string, Tone> = {
  // Owner-pilot admission states.
  COMPLETED: 'green',
  FAILED: 'red',
  REJECTED: 'red',
  DENIED: 'red',
  CANCELLED: 'muted',
  ADMITTED: 'signal',
  RUNNING: 'signal',
  UNCERTAIN: 'amber',
  // Kernel TaskStatus.
  pending: 'muted',
  running: 'signal',
  blocked: 'amber',
  failed: 'red',
  completed: 'green',
  // Runtime AgentStatus.
  idle: 'muted',
  busy: 'signal',
  draining: 'amber',
  offline: 'muted',
  error: 'red',
  // SkillStatus.
  DRAFT: 'muted',
  TESTING: 'amber',
  APPROVED: 'signal',
  PUBLISHED: 'green',
  DEPRECATED: 'amber',
  RETIRED: 'muted',
  // VerificationState / evidence.
  unverified: 'muted',
  in_progress: 'signal',
  partially_verified: 'amber',
  verified: 'green',
  disproven: 'red',
  // Severity.
  negligible: 'muted',
  low: 'neutral',
  medium: 'amber',
  high: 'red',
  critical: 'red',
  // Audit results.
  allowed: 'green',
  denied: 'red',
}

export function stateTone(state: string | null | undefined): Tone {
  if (!state) return 'muted'
  return STATE_TONES[state.toLowerCase()] ?? STATE_TONES[state] ?? 'muted'
}
