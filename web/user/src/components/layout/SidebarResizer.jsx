import { useResizable } from '@shared/hooks/useResizable'
import useSidebarStore from '../../stores/sidebarStore'

// I2 detent: dragging the handle well under the 180px minimum (cursor intent
// below 120px) collapses the sidebar to the 48px icon rail on release — the
// rail transition itself is the existing 220ms sidebar width spring.
const COLLAPSE_INTENT_PX = 120

export default function SidebarResizer() {
  const width = useSidebarStore((s) => s.width)
  const setWidth = useSidebarStore((s) => s.setWidth)
  const setCollapsed = useSidebarStore((s) => s.setCollapsed)
  const toggleCollapsed = useSidebarStore((s) => s.toggleCollapsed)

  const { dragging, onMouseDown } = useResizable({
    initial: width,
    min: 180,
    max: 480,
    direction: 'right',
    onResize: setWidth,
    onRelease: (size, unclamped) => {
      if (unclamped < COLLAPSE_INTENT_PX) setCollapsed(true)
    },
  })

  return (
    <div
      onMouseDown={onMouseDown}
      onDoubleClick={toggleCollapsed}
      style={{
        position: 'absolute',
        right: 0,
        top: 0,
        bottom: 0,
        width: 4,
        cursor: 'col-resize',
        background: dragging ? 'var(--blue)' : 'transparent',
        transition: 'background 100ms ease',
        zIndex: 10,
      }}
      onMouseEnter={(e) => {
        if (!dragging) e.currentTarget.style.background = 'var(--blue)'
      }}
      onMouseLeave={(e) => {
        if (!dragging) e.currentTarget.style.background = 'transparent'
      }}
    />
  )
}
