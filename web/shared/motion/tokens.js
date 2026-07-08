// Single source of truth for motion timing + easing (anime.js v4).
//
// IMPORTANT (verified against installed animejs@4.5.0):
// - Ease STRINGS like 'cubicBezier(...)' were removed in v4 — they warn and
//   silently fall back to LINEAR. Always import and pass the function.
// - Durations are in MILLISECONDS (framer-motion used seconds).
import { cubicBezier } from 'animejs'

// Design-spec budgets (CLAUDE.md): hover 150 / panels 200 / sidebar+canvas 220.
export const DURATION = { fast: 100, hover: 150, panel: 200, canvas: 220 }

// Migration-preserved timings carried over verbatim from framer-motion / CSS.
// Some are off-spec by explicit user decision (behavior-preserving P1);
// the deferred timing-normalization pass edits ONLY this object.
export const DUR_MIGRATION = {
  accordionHeight: 300, // AnimatedCollapse mode A height
  accordionOpacity: 200, // AnimatedCollapse mode A opacity
  accordionModeB: 200, // AnimatedCollapse mode B measured-height
  chevron: 250, // AnimatedChevron rotate
  tabSlide: 250, // SlidingTabIndicator FLIP
  toolReveal: 160, // ToolRunSection compact reveal
  treeExpand: 320, // SkillList inline tree open
  treeCollapse: 420, // SkillList inline tree close
  topologyFill: 150, // SystemTopologyDiagram status rect fill
}

// anime.js ease functions
export const EASE_SPRING = cubicBezier(0.16, 1, 0.3, 1) // mirrors --ease-spring (index.css)
export const EASE_ACCORDION = cubicBezier(0.22, 1, 0.36, 1) // migration-preserved (ACCORDION_EASE)
export const EASE_TAB = cubicBezier(0.4, 0, 0.2, 1) // migration-preserved (tabs + mode B)
export const EASE_OUT = cubicBezier(0, 0, 0.58, 1) // framer 'easeOut'
export const EASE_IN_OUT = cubicBezier(0.42, 0, 0.58, 1) // framer keyframes default

// CSS-string twins for plain CSS transitions
export const EASE_SPRING_CSS = 'cubic-bezier(0.16, 1, 0.3, 1)'
export const EASE_ACCORDION_CSS = 'cubic-bezier(0.22, 1, 0.36, 1)'
export const EASE_TAB_CSS = 'cubic-bezier(0.4, 0, 0.2, 1)'
export const EASE_IN_OUT_CSS = 'cubic-bezier(0.42, 0, 0.58, 1)'
