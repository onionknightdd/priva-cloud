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
  const pageRefs = useRef([])
  const previousPageRef = useRef(0)
  const animationsRef = useRef([])
  const reducedMotion = useReducedMotion()

  useEffect(() => () => {
    animationsRef.current.forEach((animation) => animation.cancel())
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

  return (
    <div className="flex flex-col min-w-0" style={{ gap: 8 }}>
      <div
        className="grid min-w-0"
        style={{
          gridTemplateColumns: 'minmax(0, 1fr)',
          gridTemplateRows: 'auto',
          alignItems: 'start',
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
      </div>

      <div
        className="flex items-center justify-center"
        role="tablist"
        aria-label="Homepage overview pages"
        style={{ gap: 2 }}
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
              onClick={() => setActivePage(index)}
              className="flex items-center justify-center"
              style={{
                width: 20,
                height: 20,
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
  )
}
