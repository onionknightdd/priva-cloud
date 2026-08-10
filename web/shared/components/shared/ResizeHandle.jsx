import { useState } from 'react'

const activeGradient = (orientation) => `linear-gradient(
  to ${orientation === 'horizontal' ? 'right' : 'bottom'},
  transparent 0%,
  color-mix(in srgb, var(--blue) 70%, transparent) 10%,
  var(--blue) 44%,
  var(--blue) 56%,
  color-mix(in srgb, var(--blue) 70%, transparent) 90%,
  transparent 100%
)`

function linePosition(orientation, edge, thickness) {
  if (orientation === 'horizontal') {
    const position = { left: 0, right: 0, height: thickness }
    if (edge === 'start') position.top = 0
    else if (edge === 'end') position.bottom = 0
    else {
      position.top = '50%'
      position.transform = 'translateY(-50%)'
    }
    return position
  }

  const position = { top: 0, bottom: 0, width: thickness }
  if (edge === 'start') position.left = 0
  else if (edge === 'end') position.right = 0
  else {
    position.left = '50%'
    position.transform = 'translateX(-50%)'
  }
  return position
}

function Indicator({ orientation, edge, corner, active, showIdle }) {
  if (orientation === 'corner') {
    return (
      <>
        <Indicator
          orientation="horizontal"
          edge={corner.includes('n') ? 'start' : 'end'}
          active={active}
          showIdle={showIdle}
        />
        <Indicator
          orientation="vertical"
          edge={corner.includes('w') ? 'start' : 'end'}
          active={active}
          showIdle={showIdle}
        />
      </>
    )
  }

  const shared = {
    position: 'absolute',
    pointerEvents: 'none',
    transition: 'opacity 200ms ease',
  }

  return (
    <>
      {showIdle && (
        <span
          aria-hidden="true"
          style={{
            ...shared,
            ...linePosition(orientation, edge, 1),
            background: 'var(--border)',
            opacity: active ? 0 : 1,
          }}
        />
      )}
      <span
        aria-hidden="true"
        style={{
          ...shared,
          ...linePosition(orientation, edge, 2),
          background: activeGradient(orientation),
          opacity: active ? 1 : 0,
        }}
      />
    </>
  )
}

export default function ResizeHandle({
  as: Component = 'div',
  orientation = 'vertical',
  edge = 'center',
  corner = 'se',
  dragging = false,
  active: activeProp = false,
  disabled = false,
  showIdle = true,
  style,
  onMouseEnter,
  onMouseLeave,
  children,
  ...props
}) {
  const [hovered, setHovered] = useState(false)
  const active = !disabled && (hovered || dragging || activeProp)
  const isHorizontal = orientation === 'horizontal'
  const isCorner = orientation === 'corner'
  const cornerCursor = corner === 'ne' || corner === 'sw' ? 'nesw-resize' : 'nwse-resize'

  return (
    <Component
      {...props}
      style={{
        position: 'absolute',
        width: isHorizontal ? undefined : isCorner ? 12 : 4,
        height: isHorizontal ? 4 : isCorner ? 12 : undefined,
        padding: 0,
        border: 'none',
        background: 'transparent',
        cursor: disabled ? 'default' : isCorner ? cornerCursor : isHorizontal ? 'row-resize' : 'col-resize',
        ...style,
      }}
      onMouseEnter={(event) => {
        setHovered(true)
        onMouseEnter?.(event)
      }}
      onMouseLeave={(event) => {
        setHovered(false)
        onMouseLeave?.(event)
      }}
    >
      {children}
      <Indicator orientation={orientation} edge={edge} corner={corner} active={active} showIdle={showIdle} />
    </Component>
  )
}
