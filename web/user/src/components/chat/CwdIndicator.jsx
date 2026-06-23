import { FolderGit2 } from 'lucide-react'

export default function CwdIndicator({ cwd }) {
  // Always render the chip. Before a real cwd resolves (e.g. while the agent
  // sandbox is waking, or in an empty chat with no session yet) show '~' — the
  // agent-runner resolves '~' to the user's workspace, and the chip fills with
  // the real path (from the agent-runner) once it responds.
  const display = cwd || '~'

  return (
    <div
      className="inline-flex max-w-full"
      title={display}
    >
      <div
        className="inline-flex items-center gap-1 min-w-0"
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: 4,
          color: 'var(--text-secondary)',
          fontSize: 12,
          lineHeight: 1.2,
          maxWidth: '100%',
          height: 28,
          padding: '0 9px',
        }}
      >
        <FolderGit2 size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />
        <span
          style={{
            minWidth: 0,
            maxWidth: '100%',
            display: 'block',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            lineHeight: 1.2,
          }}
        >
          {display}
        </span>
      </div>
    </div>
  )
}
