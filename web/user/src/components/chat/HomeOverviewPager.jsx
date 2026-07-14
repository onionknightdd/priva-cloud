import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { animate } from 'animejs'
import useReducedMotion from '@shared/motion/useReducedMotion'
import { EASE_SPRING } from '@shared/motion/tokens'
import RecentActivities from './RecentActivities'
import UsageStatsOverview from './UsageStatsOverview'

const PAGES = [
  { id: 'recent', label: 'Recent activities' },
  { id: 'usage', label: 'Usage status' },
]

export default function HomeOverviewPager() {
  const [activePage, setActivePage] = useState(0)
  const [pageWidth, setPageWidth] = useState(null)
  const rootRef = useRef(null)
  const pageRefs = useRef([])
  const previousPageRef = useRef(0)
  const animationsRef = useRef([])
  const dragRef = useRef(null)
  const wheelRef = useRef({ deltaX: 0, resetTimer: null })
  const suppressClickRef = useRef(false)
  const reducedMotion = useReducedMotion()

  useEffect(() => () => {
    animationsRef.current.forEach((animation) => animation.cancel())
    if (wheelRef.current.resetTimer) window.clearTimeout(wheelRef.current.resetTimer)
  }, [])

  // Preserve the usage card's old two-column width on wide tracks. Below the
  // original grid's 660px two-column threshold, both pages become full width.
  useLayoutEffect(() => {
    const root = rootRef.current
    if (!root || typeof ResizeObserver === 'undefined') return undefined

    const updateWidth = (width) => {
      const nextWidth = Math.round(width >= 660 ? (width - 20) / 2 : width)
      setPageWidth((current) => current === nextWidth ? current : nextWidth)
    }
    updateWidth(root.clientWidth)

    const observer = new ResizeObserver((entries) => {
      updateWidth(entries[0]?.contentRect.width || root.clientWidth)
    })
    observer.observe(root)
    return () => observer.disconnect()
  }, [])

  useLayoutEffect(() => {
    const pages = pageRefs.current
    const incoming = pages[activePage]
    const previousPage = previousPageRef.current
    const outgoing = pages[previousPage]
    if (!incoming) return

    animationsRef.current.forEach((animation) => animation.cancel())
    animationsRef.current = []

    if (previousPage === activePage || reducedMotion || !outgoing) {
      pages.forEach((page, index) => {
        if (!page) return
        page.style.opacity = index === activePage ? '1' : '0'
        page.style.transform = 'translateX(0px)'
        page.style.visibility = index === activePage ? 'visible' : 'hidden'
        page.style.zIndex = index === activePage ? '1' : '0'
      })
      previousPageRef.current = activePage
      return
    }

    const direction = activePage > previousPage ? 1 : -1
    pages.forEach((page, index) => {
      if (!page || index === activePage || index === previousPage) return
      page.style.opacity = '0'
      page.style.transform = 'translateX(0px)'
      page.style.visibility = 'hidden'
      page.style.zIndex = '0'
    })

    incoming.style.visibility = 'visible'
    incoming.style.zIndex = '2'
    incoming.style.opacity = '0'
    incoming.style.transform = `translateX(${16 * direction}px)`
    outgoing.style.visibility = 'visible'
    outgoing.style.zIndex = '1'
    outgoing.style.opacity = '1'
    outgoing.style.transform = 'translateX(0px)'

    animationsRef.current = [
      animate(incoming, {
        opacity: 1,
        translateX: '0px',
        duration: 200,
        ease: EASE_SPRING,
      }),
      animate(outgoing, {
        opacity: 0,
        translateX: `${-16 * direction}px`,
        duration: 180,
        ease: EASE_SPRING,
        onComplete: () => {
          outgoing.style.visibility = 'hidden'
          outgoing.style.transform = 'translateX(0px)'
          outgoing.style.zIndex = '0'
        },
      }),
    ]
    previousPageRef.current = activePage
  }, [activePage, reducedMotion])

  const resetDraggedPage = () => {
    const page = pageRefs.current[activePage]
    if (!page || reducedMotion) return
    animate(page, { translateX: '0px', duration: 150, ease: EASE_SPRING })
  }

  const moveToPage = (nextPage) => {
    if (nextPage < 0 || nextPage >= PAGES.length || nextPage === activePage) {
      resetDraggedPage()
      return
    }
    setActivePage(nextPage)
  }

  const handlePointerDown = (event) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return
    animationsRef.current.forEach((animation) => animation.cancel())
    animationsRef.current = []
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      deltaX: 0,
      direction: null,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handlePointerMove = (event) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return

    const deltaX = event.clientX - drag.startX
    const deltaY = event.clientY - drag.startY
    if (!drag.direction && Math.hypot(deltaX, deltaY) > 8) {
      drag.direction = Math.abs(deltaX) > Math.abs(deltaY) ? 'horizontal' : 'vertical'
    }
    if (drag.direction !== 'horizontal') return

    event.preventDefault()
    drag.deltaX = deltaX
    const page = pageRefs.current[activePage]
    if (!page || reducedMotion) return
    const offset = Math.max(-56, Math.min(56, deltaX * 0.35))
    page.style.transform = `translateX(${offset}px)`
  }

  const finishPointerDrag = (event) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    dragRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }

    if (drag.direction === 'horizontal' && Math.abs(drag.deltaX) >= 56) {
      suppressClickRef.current = true
      moveToPage(activePage + (drag.deltaX < 0 ? 1 : -1))
      return
    }
    resetDraggedPage()
  }

  const handleWheel = (event) => {
    if (Math.abs(event.deltaX) <= Math.abs(event.deltaY) || !event.deltaX) return
    event.preventDefault()

    const wheel = wheelRef.current
    wheel.deltaX += event.deltaX
    if (wheel.resetTimer) window.clearTimeout(wheel.resetTimer)
    wheel.resetTimer = window.setTimeout(() => {
      wheel.deltaX = 0
      wheel.resetTimer = null
    }, 120)

    if (Math.abs(wheel.deltaX) >= 56) {
      const direction = wheel.deltaX < 0 ? 1 : -1
      wheel.deltaX = 0
      moveToPage(activePage + direction)
    }
  }

  return (
    <div ref={rootRef} className="flex flex-col min-w-0" style={{ width: '100%', alignItems: 'flex-start' }}>
      <div
        className="grid min-w-0"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishPointerDrag}
        onPointerCancel={finishPointerDrag}
        onPointerLeave={(event) => {
          if (dragRef.current?.pointerId === event.pointerId && event.buttons === 0) finishPointerDrag(event)
        }}
        onWheel={handleWheel}
        onClickCapture={(event) => {
          if (!suppressClickRef.current) return
          event.preventDefault()
          event.stopPropagation()
          suppressClickRef.current = false
        }}
        style={{
          width: pageWidth ? `${pageWidth}px` : '100%',
          gridTemplateColumns: 'minmax(0, 1fr)',
          gridTemplateRows: 'auto',
          alignItems: 'start',
          position: 'relative',
          paddingBottom: 28,
          touchAction: 'pan-y',
        }}
      >
        {PAGES.map((page, index) => {
          const active = index === activePage
          return (
            <section
              key={page.id}
              ref={(element) => { pageRefs.current[index] = element }}
              aria-hidden={!active}
              className="flex flex-col min-w-0"
              style={{
                gridColumn: 1,
                gridRow: 1,
                gap: 8,
                opacity: active ? 1 : 0,
                transform: 'translateX(0px)',
                visibility: active ? 'visible' : 'hidden',
                pointerEvents: active ? 'auto' : 'none',
                zIndex: active ? 1 : 0,
                willChange: 'transform, opacity',
              }}
            >
              <div
                className="font-semibold"
                style={{ color: 'var(--text-primary)', fontSize: 12, lineHeight: 1.3 }}
              >
                {page.label}
              </div>
              {page.id === 'usage'
                ? <UsageStatsOverview showTitle={false} />
                : <RecentActivities showTitle={false} />}
            </section>
          )
        })}
        <div
          className="flex items-center justify-center"
          role="tablist"
          aria-label="Homepage overview pages"
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            bottom: 2,
            gap: 0,
          }}
        >
          {PAGES.map((page, index) => {
            const active = index === activePage
            return (
              <button
                key={page.id}
                type="button"
                role="tab"
                aria-label={page.label}
                aria-selected={active}
                title={page.label}
                onClick={() => moveToPage(index)}
                className="flex items-center justify-center"
                style={{
                  width: 16,
                  height: 18,
                  padding: 0,
                  border: 'none',
                  background: 'transparent',
                  cursor: active ? 'default' : 'pointer',
                }}
              >
                <span
                  aria-hidden="true"
                  style={{
                    width: active ? 7 : 6,
                    height: active ? 7 : 6,
                    borderRadius: 4,
                    background: active ? 'var(--blue)' : 'var(--text-dim)',
                    transition: 'width 150ms ease, height 150ms ease, background 150ms ease',
                  }}
                />
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
