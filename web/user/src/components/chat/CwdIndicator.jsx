import { FolderGit2 } from 'lucide-react'

// The working-directory chip. Before a conversation starts it is a button that
// opens the directory picker; once the conversation is underway (cwd locked) it
// renders as a plain, non-interactive chip (no lock glyph — disabled click only).
export default function CwdIndicator({ cwd, onClick, disabled = false }) {
  // Show '~' until a real cwd resolves (sandbox waking / empty new chat). The
  // agent-runner resolves '~' to the user's workspace.
  const display = cwd || '~'
  const interactive = !!onClick && !disabled

  const chipStyle = {
    background: 'var(--bg-surface)',
    border: '1px solid var(--border)',
    borderRadius: 4,
    color: 'var(--text-secondary)',
    fontSize: 12,
    lineHeight: 1.2,
    maxWidth: '100%',
    height: 28,
    padding: '0 9px',
    boxSizing: 'border-box',
    cursor: interactive ? 'pointer' : 'default',
    transition: 'border-color 150ms ease, color 150ms ease',
  }

  const inner = (
    <>
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
    </>
  )

  if (interactive) {
    return (
      <button
        type="button"
        className="inline-flex items-center gap-1 min-w-0"
        title={display}
        onClick={onClick}
        style={chipStyle}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = 'var(--border-strong)'
          e.currentTarget.style.color = 'var(--text-primary)'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = 'var(--border)'
          e.currentTarget.style.color = 'var(--text-secondary)'
        }}
      >
        {inner}
      </button>
    )
  }

  return (
    <div className="inline-flex max-w-full" title={display}>
      <div className="inline-flex items-center gap-1 min-w-0" style={chipStyle}>
        {inner}
      </div>
    </div>
  )
}
