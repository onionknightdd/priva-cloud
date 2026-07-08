import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { animate } from 'animejs'

import usePresence from '@shared/motion/usePresence'
import useReducedMotion from '@shared/motion/useReducedMotion'
import { EASE_SPRING, EASE_TAB } from '@shared/motion/tokens'
import { useDraggable } from './useDraggable'
import { useEdgeResizable } from './useEdgeResizable'

export const DEFAULT_FLOAT_PANE_MOTION = {
  frame: 180,
  panel: 170,
  collapse: 200,
  close: 220,
}

function toPlainRect(rect) {
  return {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
  }
}

function getDockTargetRect(el, height) {
  const parentRect = el?.parentElement?.getBoundingClientRect()
  if (parentRect) {
    return {
      left: parentRect.left,
      top: parentRect.bottom - height,
      width: parentRect.width,
      height,
    }
  }
  return {
    left: 0,
    top: Math.max(0, (typeof window !== 'undefined' ? window.innerHeight : height) - height),
    width: typeof window !== 'undefined' ? window.innerWidth : 0,
    height,
  }
}

function getExpandedTargetRect(expandMargin) {
  const width = typeof window !== 'undefined' ? window.innerWidth : expandMargin * 2
  const height = typeof window !== 'undefined' ? window.innerHeight : expandMargin * 2
  return {
    left: expandMargin,
    top: expandMargin,
    width: Math.max(0, width - expandMargin * 2),
    height: Math.max(0, height - expandMargin * 2),
  }
}

function getModeTargetRect(el, mode, bounds, dockTarget, expandMargin) {
  if (mode === 'dock') return getDockTargetRect(el, dockTarget)
  if (mode === 'expanded') return getExpandedTargetRect(expandMargin)
  return {
    left: bounds.x,
    top: bounds.y,
    width: bounds.width,
    height: bounds.height,
  }
}

function isUsableRect(rect) {
  return rect
    && Number.isFinite(rect.left)
    && Number.isFinite(rect.top)
    && Number.isFinite(rect.width)
    && Number.isFinite(rect.height)
    && rect.width > 0
    && rect.height > 0
}

function getAnchorRect(selector) {
  if (!selector || typeof document === 'undefined') return null
  const el = document.querySelector(selector)
  if (!el) return null
  const rect = toPlainRect(el.getBoundingClientRect())
  return isUsableRect(rect) ? rect : null
}

function getFallbackAnchorRect(rect) {
  const width = 24
  const height = 20
  return {
    left: rect.left + Math.max(0, rect.width - width),
    top: rect.top,
    width,
    height,
  }
}

function writeFixedRect(el, rect) {
  el.style.position = 'fixed'
  el.style.right = 'auto'
  el.style.bottom = 'auto'
  el.style.left = `${rect.left}px`
  el.style.top = `${rect.top}px`
  el.style.width = `${rect.width}px`
  el.style.height = `${rect.height}px`
}

function writeTransformBase(el) {
  el.style.transformOrigin = 'top left'
  el.style.willChange = 'transform, opacity'
}

function clearTransformBase(el) {
  el.style.transformOrigin = ''
  el.style.willChange = ''
  el.style.transform = ''
}

function animateFromRectToRect(el, from, to, options) {
  writeFixedRect(el, from)
  writeTransformBase(el)
  el.style.opacity = `${options.opacityFrom ?? 1}`
  el.style.transform = 'translateX(0px) translateY(0px) scaleX(1) scaleY(1)'
  return animate(el, {
    translateX: to.left - from.left,
    translateY: to.top - from.top,
    scaleX: to.width / from.width,
    scaleY: to.height / from.height,
    opacity: options.opacityTo ?? 1,
    duration: options.duration,
    ease: options.ease,
    onComplete: () => {
      clearTransformBase(el)
      options.onComplete?.()
    },
  })
}

function animateRectIntoPlace(el, from, to, options) {
  writeFixedRect(el, to)
  writeTransformBase(el)
  el.style.opacity = `${options.opacityFrom ?? 0}`
  el.style.transform = `translateX(${from.left - to.left}px) translateY(${from.top - to.top}px) scaleX(${from.width / to.width}) scaleY(${from.height / to.height})`
  return animate(el, {
    translateX: 0,
    translateY: 0,
    scaleX: 1,
    scaleY: 1,
    opacity: options.opacityTo ?? 1,
    duration: options.duration,
    ease: options.ease,
    onComplete: () => {
      clearTransformBase(el)
      options.onComplete?.()
    },
  })
}

function writeFrameGeometry(el, mode, bounds, dockTarget, expandMargin) {
  if (!el) return
  clearTransformBase(el)
  el.style.opacity = '1'
  if (mode === 'dock') {
    el.style.position = 'relative'
    el.style.top = ''
    el.style.left = ''
    el.style.right = ''
    el.style.bottom = ''
    el.style.width = ''
    el.style.height = `${dockTarget}px`
    return
  }
  if (mode === 'expanded') {
    el.style.position = 'fixed'
    el.style.top = `${expandMargin}px`
    el.style.left = `${expandMargin}px`
    el.style.right = `${expandMargin}px`
    el.style.bottom = `${expandMargin}px`
    el.style.width = ''
    el.style.height = ''
    return
  }
  if (mode === 'float') {
    el.style.position = 'fixed'
    el.style.top = `${bounds.y}px`
    el.style.left = `${bounds.x}px`
    el.style.right = ''
    el.style.bottom = ''
    el.style.width = `${bounds.width}px`
    el.style.height = `${bounds.height}px`
  }
}

export function useFloatPaneFrame({
  open,
  mode,
  setMode,
  minimized,
  setMinimized,
  bounds,
  setBounds,
  dockTarget,
  dockHeight,
  dockResizeDragging = false,
  minFloatWidth,
  minFloatHeight,
  expandMargin,
  minimizeAnchorSelector,
  restoreAnchorRect,
  onRestoreAnchorConsumed,
  onMotionActiveChange,
  motion = DEFAULT_FLOAT_PANE_MOTION,
}) {
  const { mounted, onExited } = usePresence(open)
  const reducedMotion = useReducedMotion()
  const frameRef = useRef(null)
  const enteredRef = useRef(false)
  const prevModeRef = useRef(mode)
  const dockAnimRef = useRef(null)
  const morphAnimRef = useRef(null)
  const minimizeAnimRef = useRef(null)
  const prevMinimizedRef = useRef(minimized)
  const preSwitchRectRef = useRef(null)
  const boundsRef = useRef(bounds)
  boundsRef.current = bounds

  const minimizedRef = useRef(minimized)
  minimizedRef.current = minimized
  const modeRef = useRef(mode)
  modeRef.current = mode
  const dockTargetRef = useRef(dockTarget)
  dockTargetRef.current = dockTarget

  const isDock = mode === 'dock'
  const isFloat = mode === 'float'
  const isExpanded = mode === 'expanded'
  const [contentVisible, setContentVisible] = useState(() => !minimized)
  const [frameVisible, setFrameVisible] = useState(() => !minimized)

  const changeMode = useCallback((next) => {
    const el = frameRef.current
    if (el) preSwitchRectRef.current = el.getBoundingClientRect()
    setMode(next)
  }, [setMode])

  useLayoutEffect(() => {
    if (!mounted || !isDock || !dockResizeDragging) return
    dockAnimRef.current?.cancel()
    dockAnimRef.current = null
    const el = frameRef.current
    if (el) el.style.height = `${dockHeight}px`
  }, [dockHeight, dockResizeDragging, isDock, mounted])

  useLayoutEffect(() => {
    const wasMinimized = prevMinimizedRef.current
    const minimizingFromVisible = !wasMinimized && minimized
    prevMinimizedRef.current = minimized

    if (!mounted) {
      enteredRef.current = false
      return
    }
    const el = frameRef.current
    if (!el) {
      if (!open) onExited()
      return
    }

    if (open && minimized) {
      if (!frameVisible && !minimizingFromVisible) {
        setContentVisible(false)
        return
      }
      const from = toPlainRect(el.getBoundingClientRect())
      const to = getAnchorRect(minimizeAnchorSelector) || getFallbackAnchorRect(from)
      dockAnimRef.current?.cancel()
      morphAnimRef.current?.cancel()
      minimizeAnimRef.current?.cancel()
      setContentVisible(false)
      onMotionActiveChange?.(true)
      const completeMinimize = () => {
        if (!minimizedRef.current) return
        setContentVisible(false)
        setFrameVisible(false)
        onMotionActiveChange?.(false)
      }
      if (reducedMotion) {
        completeMinimize()
        return
      }
      minimizeAnimRef.current = animateFromRectToRect(el, from, to, {
        opacityFrom: 1,
        opacityTo: 0,
        duration: motion.collapse,
        ease: EASE_TAB,
        onComplete: completeMinimize,
      })
      return
    }

    const previousMode = prevModeRef.current
    const modeSwitched = previousMode !== mode
    prevModeRef.current = mode
    const restoringFromMinimized = wasMinimized && !minimized

    if (reducedMotion) {
      if (open) {
        setFrameVisible(true)
        enteredRef.current = true
        if (isDock) {
          el.style.height = `${dockTarget}px`
          setContentVisible(!minimized)
        } else {
          setContentVisible(true)
          writeFrameGeometry(el, mode, boundsRef.current, dockTargetRef.current, expandMargin)
        }
        onRestoreAnchorConsumed?.()
      } else {
        onExited()
      }
      return
    }

    if (open) {
      minimizeAnimRef.current?.cancel()
      if (restoringFromMinimized) {
        const to = getModeTargetRect(el, mode, boundsRef.current, dockTargetRef.current, expandMargin)
        const from = isUsableRect(restoreAnchorRect)
          ? restoreAnchorRect
          : getAnchorRect(minimizeAnchorSelector) || getFallbackAnchorRect(to)
        dockAnimRef.current?.cancel()
        morphAnimRef.current?.cancel()
        setContentVisible(false)
        onMotionActiveChange?.(true)
        enteredRef.current = true
        morphAnimRef.current = animateRectIntoPlace(el, from, to, {
          opacityFrom: 0,
          opacityTo: 1,
          duration: motion.frame,
          ease: EASE_SPRING,
          onComplete: () => {
            if (modeRef.current !== mode || minimizedRef.current) return
            writeFrameGeometry(el, mode, boundsRef.current, dockTargetRef.current, expandMargin)
            setContentVisible(true)
            onMotionActiveChange?.(false)
            onRestoreAnchorConsumed?.()
          },
        })
        return
      }
      if (modeSwitched && preSwitchRectRef.current) {
        const from = toPlainRect(preSwitchRectRef.current)
        preSwitchRectRef.current = null
        const to = isDock
          ? getDockTargetRect(el, dockTarget)
          : toPlainRect(el.getBoundingClientRect())
        dockAnimRef.current?.cancel()
        morphAnimRef.current?.cancel()
        setContentVisible(true)
        writeFixedRect(el, from)
        morphAnimRef.current = animate(el, {
          left: `${to.left}px`,
          top: `${to.top}px`,
          width: `${to.width}px`,
          height: `${to.height}px`,
          duration: motion.frame,
          ease: mode === 'expanded' ? EASE_SPRING : EASE_TAB,
          onComplete: () => {
            if (modeRef.current !== mode) return
            writeFrameGeometry(el, mode, boundsRef.current, dockTargetRef.current, expandMargin)
            if (minimizedRef.current && modeRef.current === 'dock') setContentVisible(false)
          },
        })
        return
      }
      if (modeSwitched) preSwitchRectRef.current = null
      if (!enteredRef.current) {
        enteredRef.current = true
        if (isDock) {
          el.style.height = '0px'
          dockAnimRef.current?.cancel()
          dockAnimRef.current = animate(el, {
            height: `${dockTarget}px`,
            duration: motion.frame,
            ease: EASE_SPRING,
            onComplete: () => {
              if (minimizedRef.current && modeRef.current === 'dock') setContentVisible(false)
            },
          })
        } else {
          el.style.opacity = '0'
          el.style.transform = 'translateY(8px) scale(0.98)'
          animate(el, { opacity: 1, scale: 1, translateY: 0, duration: motion.panel, ease: EASE_SPRING })
        }
        return
      }
      if (!isDock) {
        if (!modeSwitched) animate(el, { opacity: 1, scale: 1, translateY: 0, duration: motion.panel, ease: EASE_SPRING })
        return
      }
      if (modeSwitched) {
        writeFrameGeometry(el, mode, boundsRef.current, dockTargetRef.current, expandMargin)
        return
      }
      if (dockResizeDragging) return
      const current = el.offsetHeight
      if (Math.abs(current - dockTarget) < 1) return
      const growing = dockTarget > current
      dockAnimRef.current?.cancel()
      dockAnimRef.current = animate(el, {
        height: `${dockTarget}px`,
        duration: growing ? motion.frame : motion.collapse,
        ease: growing ? EASE_SPRING : EASE_TAB,
        onComplete: () => {
          if (minimizedRef.current && modeRef.current === 'dock') setContentVisible(false)
        },
      })
    } else if (minimized) {
      setFrameVisible(false)
      setContentVisible(false)
      onExited()
    } else if (isDock) {
      dockAnimRef.current?.cancel()
      dockAnimRef.current = animate(el, {
        height: '0px',
        duration: motion.close,
        ease: EASE_TAB,
        onComplete: onExited,
      })
    } else {
      animate(el, {
        opacity: 0,
        scale: 0.98,
        duration: motion.collapse,
        ease: EASE_TAB,
        onComplete: onExited,
      })
    }
  }, [
    open,
    mounted,
    isDock,
    minimized,
    frameVisible,
    dockTarget,
    mode,
    restoreAnchorRect,
    minimizeAnchorSelector,
    reducedMotion,
    dockResizeDragging,
    onExited,
    expandMargin,
    onRestoreAnchorConsumed,
    onMotionActiveChange,
    motion,
  ])

  useEffect(() => () => {
    dockAnimRef.current?.cancel()
    morphAnimRef.current?.cancel()
    minimizeAnimRef.current?.cancel()
  }, [])

  const dragBounds = useCallback(() => ({
    minX: 0,
    minY: 0,
    maxX: (typeof window !== 'undefined' ? window.innerWidth : 9999) - 80,
    maxY: (typeof window !== 'undefined' ? window.innerHeight : 9999) - 40,
  }), [])

  const resizeBounds = useCallback(() => ({
    minX: 0,
    minY: 0,
    maxX: typeof window !== 'undefined' ? window.innerWidth : 9999,
    maxY: typeof window !== 'undefined' ? window.innerHeight : 9999,
  }), [])

  const resizeMin = useMemo(() => ({ width: minFloatWidth, height: minFloatHeight }), [minFloatHeight, minFloatWidth])
  const handleDrag = useCallback(({ x, y }) => setBounds({ x, y }), [setBounds])
  const handleResize = useCallback((rect) => setBounds(rect), [setBounds])

  const dragHandle = useDraggable({
    initial: { x: bounds.x, y: bounds.y },
    onDrag: handleDrag,
    bounds: dragBounds,
  })

  const edgeN  = useEdgeResizable({ initial: bounds, edge: 'n',  min: resizeMin, onResize: handleResize, bounds: resizeBounds })
  const edgeS  = useEdgeResizable({ initial: bounds, edge: 's',  min: resizeMin, onResize: handleResize, bounds: resizeBounds })
  const edgeE  = useEdgeResizable({ initial: bounds, edge: 'e',  min: resizeMin, onResize: handleResize, bounds: resizeBounds })
  const edgeW  = useEdgeResizable({ initial: bounds, edge: 'w',  min: resizeMin, onResize: handleResize, bounds: resizeBounds })
  const edgeNE = useEdgeResizable({ initial: bounds, edge: 'ne', min: resizeMin, onResize: handleResize, bounds: resizeBounds })
  const edgeNW = useEdgeResizable({ initial: bounds, edge: 'nw', min: resizeMin, onResize: handleResize, bounds: resizeBounds })
  const edgeSE = useEdgeResizable({ initial: bounds, edge: 'se', min: resizeMin, onResize: handleResize, bounds: resizeBounds })
  const edgeSW = useEdgeResizable({ initial: bounds, edge: 'sw', min: resizeMin, onResize: handleResize, bounds: resizeBounds })

  const lastNonExpandedRef = useRef(mode === 'expanded' ? 'float' : mode)
  useEffect(() => {
    if (mode !== 'expanded') lastNonExpandedRef.current = mode
  }, [mode])

  const restoreExpanded = useCallback(() => {
    changeMode(lastNonExpandedRef.current || 'float')
  }, [changeMode])

  const toggleExpanded = useCallback(() => {
    if (mode === 'expanded') restoreExpanded()
    else changeMode('expanded')
  }, [changeMode, mode, restoreExpanded])

  useEffect(() => {
    if (mode !== 'expanded' || minimized) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        restoreExpanded()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [minimized, mode, restoreExpanded])

  useEffect(() => {
    if (mode !== 'float') return undefined
    const onResize = () => setBounds({})
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [mode, setBounds])

  const minimize = useCallback(() => {
    setFrameVisible(true)
    setContentVisible(true)
    onMotionActiveChange?.(true)
    setMinimized(true)
  }, [onMotionActiveChange, setMinimized])

  const restore = useCallback(() => {
    setFrameVisible(true)
    setContentVisible(true)
    onMotionActiveChange?.(true)
    setMinimized(false)
  }, [onMotionActiveChange, setMinimized])

  return {
    mounted,
    frameRef,
    isDock,
    isFloat,
    isExpanded,
    isMinimized: minimized,
    frameVisible: frameVisible || !minimized || (!prevMinimizedRef.current && minimized),
    contentVisible,
    dragHandle,
    edgeHandles: { edgeN, edgeS, edgeE, edgeW, edgeNE, edgeNW, edgeSE, edgeSW },
    changeMode,
    toggleExpanded,
    restoreExpanded,
    minimize,
    restore,
  }
}

export default useFloatPaneFrame
