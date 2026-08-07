export const MAX_SESSION_TAGS = 3
export const TAG_COLOR_SLOTS = 100

const TAG_SATURATION = 0.68
const TAG_BASE_LIGHTNESS = 0.48
// Keep dark colors bright enough to separate from the dark sidebar, while
// leaving enough headroom for light text. Light colors use dark text instead.
const TAG_DARK_MIN_LUMINANCE = 0.10
const TAG_DARK_MAX_LUMINANCE = 0.14
const TAG_LIGHT_MIN_LUMINANCE = 0.24

export function normalizeSessionTags(raw) {
  const values = typeof raw === 'string' ? [raw] : (Array.isArray(raw) ? raw : [])
  const seen = new Set()
  const tags = []
  for (const value of values) {
    if (typeof value !== 'string') continue
    const tag = value.trim()
    const key = tag.toLowerCase()
    if (!tag || seen.has(key)) continue
    seen.add(key)
    tags.push(tag)
    if (tags.length === MAX_SESSION_TAGS) break
  }
  return tags
}

export function sessionTags(session) {
  if (Array.isArray(session?.tags)) return normalizeSessionTags(session.tags)
  return normalizeSessionTags(session?.tag)
}

// FNV-1a mirrors the runner's fallback for legacy responses that do not carry
// a persisted color assignment yet.
export function fallbackTagColorIndex(tag) {
  const bytes = new TextEncoder().encode(String(tag || '').trim().toLowerCase())
  let hash = 2166136261
  for (const byte of bytes) {
    hash = Math.imul(hash ^ byte, 16777619) >>> 0
  }
  return hash % TAG_COLOR_SLOTS
}

export function resolveTagColorIndex(tag, colorIndex) {
  const parsed = Number(colorIndex)
  return Number.isInteger(parsed) && parsed >= 0 && parsed < TAG_COLOR_SLOTS
    ? parsed
    : fallbackTagColorIndex(tag)
}

function relativeLuminance(hue, saturation, lightness) {
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation
  const segment = hue / 60
  const x = chroma * (1 - Math.abs((segment % 2) - 1))
  let rgb
  if (segment < 1) rgb = [chroma, x, 0]
  else if (segment < 2) rgb = [x, chroma, 0]
  else if (segment < 3) rgb = [0, chroma, x]
  else if (segment < 4) rgb = [0, x, chroma]
  else if (segment < 5) rgb = [x, 0, chroma]
  else rgb = [chroma, 0, x]
  const match = lightness - chroma / 2
  const linear = rgb.map((channel) => {
    const value = channel + match
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
}

function moveToLuminance(hue, start, target, direction) {
  let lightness = start
  for (let step = 0; step < 100; step += 1) {
    const luminance = relativeLuminance(hue, TAG_SATURATION, lightness)
    if (direction < 0 ? luminance <= target : luminance >= target) {
      return { lightness, luminance }
    }
    lightness = Math.max(0.2, Math.min(0.8, lightness + direction * 0.005))
  }
  return {
    lightness,
    luminance: relativeLuminance(hue, TAG_SATURATION, lightness),
  }
}

function buildTagColor(index) {
  const hue = index * (360 / TAG_COLOR_SLOTS)
  const baseLuminance = relativeLuminance(hue, TAG_SATURATION, TAG_BASE_LIGHTNESS)

  if (baseLuminance < TAG_DARK_MIN_LUMINANCE) {
    const adjusted = moveToLuminance(
      hue,
      TAG_BASE_LIGHTNESS,
      TAG_DARK_MIN_LUMINANCE,
      1,
    )
    return { hue, lightness: adjusted.lightness, useDarkText: false }
  }
  if (baseLuminance <= TAG_DARK_MAX_LUMINANCE) {
    return { hue, lightness: TAG_BASE_LIGHTNESS, useDarkText: false }
  }
  if (baseLuminance >= TAG_LIGHT_MIN_LUMINANCE) {
    return { hue, lightness: TAG_BASE_LIGHTNESS, useDarkText: true }
  }

  // Mid-luminance colors are the only range where neither foreground has
  // enough contrast. Move each hue to the nearest safe side of that range.
  const dark = moveToLuminance(
    hue,
    TAG_BASE_LIGHTNESS,
    TAG_DARK_MAX_LUMINANCE,
    -1,
  )
  const light = moveToLuminance(
    hue,
    TAG_BASE_LIGHTNESS,
    TAG_LIGHT_MIN_LUMINANCE,
    1,
  )
  return Math.abs(dark.lightness - TAG_BASE_LIGHTNESS)
    <= Math.abs(light.lightness - TAG_BASE_LIGHTNESS)
    ? { hue, lightness: dark.lightness, useDarkText: false }
    : { hue, lightness: light.lightness, useDarkText: true }
}

const TAG_COLORS = Array.from({ length: TAG_COLOR_SLOTS }, (_, index) => buildTagColor(index))

export function tagColorStyle(tag, colorIndex) {
  const index = resolveTagColorIndex(tag, colorIndex)
  const { hue, lightness, useDarkText } = TAG_COLORS[index]
  return {
    '--tag-hue': `${hue.toFixed(1)}deg`,
    '--tag-color-lightness': `${(lightness * 100).toFixed(1)}%`,
    background: 'hsl(var(--tag-hue) var(--tag-color-saturation) var(--tag-color-lightness))',
    color: useDarkText ? 'var(--tag-text-dark)' : 'var(--tag-text-light)',
  }
}

export function normalizeTagColorMap(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const colors = Object.create(null)
  for (const [tag, value] of Object.entries(raw)) {
    const index = Number(value)
    if (tag && Number.isInteger(index) && index >= 0 && index < TAG_COLOR_SLOTS) {
      colors[tag] = index
    }
  }
  return colors
}

export function findTagColorIndex(tag, colorMap) {
  if (!colorMap || typeof colorMap !== 'object') return fallbackTagColorIndex(tag)
  if (Object.hasOwn(colorMap, tag)) return resolveTagColorIndex(tag, colorMap[tag])
  const key = String(tag || '').toLowerCase()
  const match = Object.entries(colorMap).find(([candidate]) => candidate.toLowerCase() === key)
  return resolveTagColorIndex(tag, match?.[1])
}
