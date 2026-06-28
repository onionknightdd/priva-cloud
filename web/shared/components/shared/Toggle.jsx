// Compact switch with an ON/OFF text label, matching the existing Priva web
// plugin toggle: rectangular 4px-radius track and a square sliding thumb.
const SIZES = {
  sm: { w: 32, h: 18, knob: 12, offLeft: 2, onLeft: 16, top: 2 },
  md: { w: 36, h: 20, knob: 14, offLeft: 2, onLeft: 18, top: 2 },
}

export default function Toggle({
  checked,
  onChange,
  disabled = false,
  size = 'md',
  showLabel = true,
  ariaLabel,
  onLabel = 'ON',
  offLabel = 'OFF',
}) {
  const d = SIZES[size] || SIZES.md

  return (
    <div
      className="flex items-center gap-2"
      style={{ opacity: disabled ? 0.45 : 1, transition: 'opacity 150ms ease' }}
    >
      {showLabel && (
        <span
          className="text-xs font-semibold uppercase"
          style={{
            letterSpacing: '0.06em',
            color: checked ? 'var(--green)' : 'var(--text-dim)',
            transition: 'color 150ms ease',
            minWidth: 26,
            textAlign: 'right',
          }}
        >
          {checked ? onLabel : offLabel}
        </span>
      )}
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => { if (!disabled) onChange(!checked) }}
        style={{
          position: 'relative',
          flexShrink: 0,
          width: d.w,
          height: d.h,
          padding: 0,
          borderRadius: 4,
          border: `1px solid ${checked ? 'var(--blue)' : 'var(--border-strong)'}`,
          background: checked ? 'var(--blue)' : 'var(--bg-elevated)',
          cursor: disabled ? 'not-allowed' : 'pointer',
          boxSizing: 'border-box',
          transition: 'background 150ms ease, border-color 150ms ease',
        }}
      >
        <span
          style={{
            position: 'absolute',
            top: d.top,
            left: checked ? d.onLeft : d.offLeft,
            width: d.knob,
            height: d.knob,
            borderRadius: 2,
            background: checked ? 'var(--text-inverse)' : 'var(--text-dim)',
            transition: 'left 150ms ease, background 150ms ease',
          }}
        />
      </button>
    </div>
  )
}
