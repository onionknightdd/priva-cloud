import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { animate } from 'animejs'
import { Puzzle, Sparkles } from 'lucide-react'
import {
  Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import useAuthStore from '@shared/stores/authStore'
import { useAnimatedNumber } from '@shared/motion/useAnimatedNumber'
import useReducedMotion from '@shared/motion/useReducedMotion'
import { EASE_SPRING } from '@shared/motion/tokens'
import Tabs from '@shared/components/shared/Tabs'
import {
  AXIS_STYLE, resolveVar, useThemeKey,
} from '@shared/components/admin/charts/ChartTheme'
import useUserDataStore from '../../stores/userDataStore'

function formatNumber(value) {
  const number = Number(value) || 0
  if (number < 1000) return String(number)
  if (number < 1_000_000) return `${(number / 1000).toFixed(number < 10_000 ? 2 : 1).replace(/\.?0+$/, '')}K`
  if (number < 1_000_000_000) return `${(number / 1_000_000).toFixed(number < 10_000_000 ? 2 : 1).replace(/\.?0+$/, '')}M`
  return `${(number / 1_000_000_000).toFixed(2).replace(/\.?0+$/, '')}B`
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.floor(Math.log(value) / Math.log(1024))
  return `${(value / Math.pow(1024, index)).toFixed(index > 0 ? 1 : 0)} ${units[index]}`
}

function formatChartDate(value, locale) {
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
}

function rangeStartDate(range) {
  const days = range === '30d' ? 30 : range === '7d' ? 7 : 0
  if (!days) return null
  const date = new Date()
  date.setHours(0, 0, 0, 0)
  date.setDate(date.getDate() - days + 1)
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-')
}

function relativeTime(dateValue, t) {
  if (!dateValue) return t('admin.never')
  const elapsed = Date.now() - new Date(dateValue).getTime()
  const minutes = Math.floor(elapsed / 60000)
  if (minutes < 1) return t('admin.justNow')
  if (minutes < 60) return t('admin.minutesAgo', { count: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('admin.hoursAgo', { count: hours })
  return t('admin.daysAgo', { count: Math.floor(hours / 24) })
}

function initials(username) {
  return (username || '?').slice(0, 2).toUpperCase()
}

function Metric({ label, value, format = formatNumber, index }) {
  const animatedValue = useAnimatedNumber(value)

  return (
    <div
      className="flex flex-col items-center justify-center min-w-0"
      style={{
        minHeight: 50,
        gap: 2,
        padding: '5px 4px',
        borderLeft: index > 0 ? '1px solid var(--border-subtle)' : 'none',
      }}
    >
      <span
        className="truncate"
        style={{
          color: 'var(--text-primary)',
          fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
          fontSize: 11,
          fontWeight: 700,
          lineHeight: 1.2,
          fontVariantNumeric: 'tabular-nums',
          maxWidth: '100%',
        }}
        title={String(format(value))}
      >
        {format(animatedValue)}
      </span>
      <span
        className="truncate"
        style={{ color: 'var(--text-secondary)', fontSize: 9, lineHeight: 1.2, maxWidth: '100%' }}
      >
        {label}
      </span>
    </div>
  )
}

function TokenHeatmap({ data }) {
  const { t, i18n } = useTranslation()
  const rootRef = useRef(null)
  const animationRef = useRef(null)
  const animatedKeyRef = useRef(null)
  const reducedMotion = useReducedMotion()
  const cells = Array.isArray(data) ? data : []
  const counts = useMemo(
    () => cells.map((cell) => Number(cell.count) || 0).filter((count) => count > 0).sort((a, b) => a - b),
    [cells],
  )
  const q1 = counts[Math.floor(counts.length * 0.25)] || 0
  const q2 = counts[Math.floor(counts.length * 0.5)] || 0
  const q3 = counts[Math.floor(counts.length * 0.75)] || 0
  const dayKeys = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
  const isChinese = (i18n.resolvedLanguage || i18n.language || '').toLowerCase().startsWith('zh')

  const weeks = useMemo(() => {
    if (cells.length === 0) return []
    const firstDate = new Date(`${cells[0].date}T00:00:00`)
    const firstDay = (firstDate.getDay() + 6) % 7
    const padded = Array(firstDay).fill(null).concat(cells)
    const groups = []
    for (let index = 0; index < padded.length; index += 7) {
      groups.push(padded.slice(index, index + 7))
    }
    return groups
  }, [cells])

  const monthLabels = useMemo(() => {
    const seen = new Set()
    const locale = i18n.resolvedLanguage || i18n.language || undefined
    return weeks.map((week) => {
      const first = week.find(Boolean)
      if (!first) return ''
      const month = first.date.slice(0, 7)
      if (seen.has(month)) return ''
      seen.add(month)
      return new Date(`${first.date}T00:00:00`).toLocaleString(locale, { month: 'short' })
    })
  }, [i18n.language, i18n.resolvedLanguage, weeks])

  const animationKey = useMemo(
    () => cells.map((cell) => `${cell.date}:${cell.count}`).join('|'),
    [cells],
  )

  useLayoutEffect(() => {
    const root = rootRef.current
    if (!root || !animationKey || animatedKeyRef.current === animationKey) return undefined

    const heatmapCells = Array.from(root.querySelectorAll('[data-token-heatmap-cell]'))
    animatedKeyRef.current = animationKey
    animationRef.current?.cancel()

    if (reducedMotion) {
      heatmapCells.forEach((cell) => {
        cell.style.opacity = '1'
        cell.style.transform = ''
      })
      return undefined
    }

    // The DOM is column-major (each week contains its seven days), so a shared
    // per-column delay produces a clean left-to-right fill without layout work.
    heatmapCells.forEach((cell) => {
      cell.style.opacity = '0'
      cell.style.transform = 'scale(0.62)'
      cell.style.willChange = 'opacity, transform'
    })
    animationRef.current = animate(heatmapCells, {
      opacity: 1,
      scale: 1,
      duration: 240,
      delay: (_, index) => Math.floor(index / 7) * 15,
      ease: EASE_SPRING,
      onComplete: () => {
        heatmapCells.forEach((cell) => {
          cell.style.opacity = '1'
          cell.style.transform = ''
          cell.style.willChange = ''
        })
      },
    })

    return () => {
      animationRef.current?.cancel()
      if (animatedKeyRef.current === animationKey) animatedKeyRef.current = null
    }
  }, [animationKey, reducedMotion])

  const bucket = (count) => {
    if (count <= 0) return 0
    if (count <= q1) return 1
    if (count <= q2) return 2
    if (count <= q3) return 3
    return 4
  }

  const background = (level) => {
    if (level === 0) return 'var(--bg-elevated)'
    const alphas = [0, 0.3, 0.48, 0.68, 0.9]
    return `color-mix(in srgb, var(--blue) ${alphas[level] * 100}%, var(--bg-elevated))`
  }

  const formatDay = (key) => {
    const label = t(`chat.usage.heatmap.day.${key}`)
    return isChinese ? label.replace(/^周/, '').slice(-1) : label.slice(0, 3)
  }

  if (weeks.length === 0) return null

  return (
    <div ref={rootRef} className="min-w-0" style={{ width: '100%' }}>
      <div className="flex min-w-0" style={{ gap: 6, alignItems: 'stretch' }}>
        <div
          className="grid flex-shrink-0"
          style={{ width: 22, gridTemplateRows: 'repeat(7, minmax(0, 1fr))', rowGap: 3 }}
        >
          {dayKeys.map((dayKey) => (
            <span
              key={dayKey}
              className="flex items-center justify-end"
              style={{ color: 'var(--text-dim)', fontSize: 9, lineHeight: 1, paddingRight: 2 }}
            >
              {formatDay(dayKey)}
            </span>
          ))}
        </div>
        <div
          className="grid flex-1 min-w-0"
          style={{
            gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))`,
            columnGap: 3,
            rowGap: 3,
          }}
        >
          {weeks.map((week, weekIndex) => (
            <div key={weekIndex} className="flex flex-col" style={{ gap: 3, minWidth: 0 }}>
              {Array.from({ length: 7 }, (_, dayIndex) => {
                const cell = week[dayIndex]
                if (!cell) return <div key={`${weekIndex}-${dayIndex}`} style={{ aspectRatio: '1 / 1' }} />
                const level = bucket(cell.count)
                return (
                  <div
                    key={cell.date}
                    data-token-heatmap-cell
                    title={t('chat.usage.heatmap.tooltip', { date: cell.date, count: formatNumber(cell.count) })}
                    style={{
                      aspectRatio: '1 / 1',
                      background: background(level),
                      border: level === 0 ? '1px solid var(--border-subtle)' : '1px solid transparent',
                      borderRadius: 2,
                    }}
                  />
                )
              })}
            </div>
          ))}
        </div>
      </div>
      <div
        className="grid min-w-0"
        style={{
          gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))`,
          columnGap: 3,
          marginTop: 8,
          marginLeft: 28,
        }}
      >
        {monthLabels.map((label, index) => (
          <span
            key={index}
            style={{
              color: 'var(--text-secondary)',
              fontSize: 11,
              lineHeight: 1.2,
              minWidth: 0,
              overflow: 'visible',
              textAlign: 'left',
              whiteSpace: 'nowrap',
            }}
          >
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}

function ModelUsageTooltip({ active, payload, label, colorForModel }) {
  if (!active || !payload?.length) return null

  const items = payload
    .map((entry) => ({ model: entry.dataKey || entry.name, tokens: Number(entry.value) || 0 }))
    .filter((entry) => entry.tokens > 0)
    .sort((left, right) => right.tokens - left.tokens)

  if (items.length === 0) return null

  return (
    <div
      className="flex flex-col min-w-0"
      style={{
        minWidth: 180,
        maxWidth: 280,
        background: resolveVar('--bg-elevated'),
        border: `1px solid ${resolveVar('--border')}`,
        borderRadius: 4,
        padding: '8px 10px',
        gap: 5,
      }}
    >
      <div style={{ color: resolveVar('--text-secondary'), fontSize: 10, lineHeight: 1.2 }}>
        {label}
      </div>
      {items.map((item) => (
        <div key={item.model} className="flex items-center gap-2 min-w-0" style={{ fontSize: 11 }}>
          <span
            style={{ width: 7, height: 7, borderRadius: 1, background: colorForModel(item.model), flexShrink: 0 }}
          />
          <span className="truncate" style={{ color: resolveVar('--text-secondary'), flex: 1 }} title={item.model}>
            {item.model}
          </span>
          <span
            style={{
              color: resolveVar('--text-primary'),
              fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
              fontSize: 10,
              fontVariantNumeric: 'tabular-nums',
              flexShrink: 0,
            }}
          >
            {formatNumber(item.tokens)}
          </span>
        </div>
      ))}
    </div>
  )
}

function ModelsView({ modelUsage, dailyModelTokens, range, themeKey }) {
  const { t, i18n } = useTranslation()
  const [legendScrollbarVisible, setLegendScrollbarVisible] = useState(false)
  const legendScrollbarTimerRef = useRef(null)
  const rangeStart = useMemo(() => rangeStartDate(range), [range])
  const filteredDailyModelTokens = useMemo(
    () => (dailyModelTokens || []).filter((item) => !rangeStart || item.date >= rangeStart),
    [dailyModelTokens, rangeStart],
  )
  const chartData = useMemo(
    () => filteredDailyModelTokens.map((item) => ({ date: item.date, ...item.by_model })),
    [filteredDailyModelTokens],
  )
  const allUsageByModel = useMemo(
    () => new Map((modelUsage || []).map((item) => [item.model, item])),
    [modelUsage],
  )
  const sortedModels = useMemo(
    () => {
      const totals = new Map()
      filteredDailyModelTokens.forEach((item) => {
        Object.entries(item.by_model || {}).forEach(([model, tokens]) => {
          totals.set(model, (totals.get(model) || 0) + (Number(tokens) || 0))
        })
      })
      const totalTokens = Array.from(totals.values()).reduce((sum, tokens) => sum + tokens, 0)
      return Array.from(totals, ([model, total_tokens]) => {
        const allUsage = allUsageByModel.get(model)
        return {
          model,
          total_tokens,
          input_tokens: range === 'all' ? allUsage?.input_tokens ?? 0 : null,
          output_tokens: range === 'all' ? allUsage?.output_tokens ?? 0 : null,
          percentage: totalTokens > 0 ? (total_tokens / totalTokens) * 100 : 0,
        }
      }).sort((left, right) => right.total_tokens - left.total_tokens || left.model.localeCompare(right.model))
    },
    [allUsageByModel, filteredDailyModelTokens, range],
  )
  const modelNames = useMemo(() => sortedModels.map((item) => item.model), [sortedModels])
  const hasScrollableLegend = sortedModels.length > 3
  const colorFor = (index) => {
    const depth = modelNames.length <= 1 ? 100 : 100 - ((index / (modelNames.length - 1)) * 64)
    return `color-mix(in srgb, var(--blue) ${depth}%, var(--bg-surface))`
  }
  const colorForModel = (model) => colorFor(Math.max(0, modelNames.indexOf(model)))

  useEffect(() => () => clearTimeout(legendScrollbarTimerRef.current), [])

  const revealLegendScrollbar = () => {
    if (!hasScrollableLegend) return
    clearTimeout(legendScrollbarTimerRef.current)
    setLegendScrollbarVisible(true)
  }

  const hideLegendScrollbar = (delay = 700) => {
    if (!hasScrollableLegend) return
    clearTimeout(legendScrollbarTimerRef.current)
    legendScrollbarTimerRef.current = setTimeout(() => setLegendScrollbarVisible(false), delay)
  }

  if (chartData.length === 0 || modelNames.length === 0) {
    return (
      <div className="text-xs" style={{ color: 'var(--text-dim)', padding: '24px 0', textAlign: 'center' }}>
        {t('chat.usage.models.noData')}
      </div>
    )
  }

  return (
    <div className="flex flex-col min-w-0" style={{ gap: 12 }}>
      <div style={{ width: '100%', height: 142 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart key={`usage-models-${themeKey}-${range}`} data={chartData} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
            <XAxis
              dataKey="date"
              {...AXIS_STYLE}
              height={21}
              tick={{ ...AXIS_STYLE.tick, fill: resolveVar('--text-dim'), fontSize: 9 }}
              tickFormatter={(value) => formatChartDate(value, i18n.resolvedLanguage || i18n.language || undefined)}
            />
            <YAxis
              {...AXIS_STYLE}
              width={42}
              tickMargin={4}
              tick={{ ...AXIS_STYLE.tick, fill: resolveVar('--text-dim'), fontSize: 9 }}
              allowDecimals={false}
              tickFormatter={formatNumber}
            />
            <Tooltip content={<ModelUsageTooltip colorForModel={colorForModel} />} cursor={{ fill: resolveVar('--bg-elevated') }} />
            {modelNames.map((model, index) => (
              <Bar key={model} dataKey={model} stackId="tokens" name={model} fill={colorFor(index)} radius={2} animationDuration={400} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div
        className={`flex flex-col ${hasScrollableLegend ? 'model-usage-legend-scroll' : ''} ${legendScrollbarVisible ? 'model-usage-legend-scroll-visible' : ''}`}
        onMouseEnter={revealLegendScrollbar}
        onMouseLeave={() => hideLegendScrollbar(250)}
        onFocus={revealLegendScrollbar}
        onBlur={() => hideLegendScrollbar()}
        onScroll={() => {
          revealLegendScrollbar()
          hideLegendScrollbar()
        }}
        style={{
          height: hasScrollableLegend ? 60 : 'auto',
          maxHeight: 60,
          overflowY: hasScrollableLegend ? 'auto' : 'hidden',
          gap: 6,
          paddingRight: hasScrollableLegend ? 2 : 0,
        }}
      >
        {sortedModels.map((model, index) => (
          <div key={model.model} className="flex items-center gap-2 min-w-0" style={{ minHeight: 16, fontSize: 11 }}>
            <span style={{ width: 7, height: 7, borderRadius: 2, background: colorFor(index), flexShrink: 0 }} />
            <span className="truncate" style={{ color: 'var(--text-primary)', flex: 1 }} title={model.model}>
              {model.model}
            </span>
            <span style={{ color: 'var(--text-dim)', fontSize: 10, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
              {range === 'all'
                ? t('chat.usage.models.inOut', { input: formatNumber(model.input_tokens), output: formatNumber(model.output_tokens) })
                : t('chat.usage.models.total', { total: formatNumber(model.total_tokens) })}
            </span>
            <span style={{ color: 'var(--text-secondary)', fontSize: 10, width: 42, textAlign: 'right', flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
              {model.percentage.toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function SkillsList({ items }) {
  const { t } = useTranslation()
  const skills = Array.isArray(items) ? items : []

  if (skills.length === 0) {
    return (
      <div className="text-xs" style={{ color: 'var(--text-dim)', padding: '18px 0', textAlign: 'center' }}>
        {t('chat.usage.skills.noData')}
      </div>
    )
  }

  return (
    <div className="flex flex-col" style={{ gap: 8 }}>
      {skills.map((item) => (
        <div key={item.skill} className="flex items-center gap-2 min-w-0">
          <span
            className="flex items-center justify-center flex-shrink-0"
            style={{ width: 22, height: 22, background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)', borderRadius: 4, color: 'var(--blue)' }}
          >
            <Puzzle size={13} strokeWidth={1.5} />
          </span>
          <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 12, fontWeight: 600, flex: 1 }} title={item.skill}>
            /{item.skill}
          </span>
          <span
            style={{ color: 'var(--text-secondary)', fontSize: 11, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}
          >
            {t('chat.usage.skills.runs', { count: formatNumber(item.count) })}
          </span>
        </div>
      ))}
    </div>
  )
}

function WorkspaceMetric({ label, value, format, index }) {
  const numeric = typeof value === 'number' && Number.isFinite(value)
  const animatedValue = useAnimatedNumber(numeric ? value : 0)
  const shown = numeric ? format(animatedValue) : value

  return (
    <div
      className="flex flex-col items-center justify-center min-w-0"
      style={{
        minHeight: 58,
        gap: 3,
        padding: '7px 10px',
        borderLeft: index > 0 ? '1px solid var(--border-subtle)' : 'none',
      }}
    >
      <span
        className="truncate"
        style={{ color: 'var(--text-primary)', fontSize: 12, fontWeight: 600, lineHeight: 1.25, maxWidth: '100%', fontVariantNumeric: 'tabular-nums' }}
        title={String(shown)}
      >
        {shown}
      </span>
      <span
        className="truncate"
        style={{ color: 'var(--text-secondary)', fontSize: 10, lineHeight: 1.2, maxWidth: '100%' }}
      >
        {label}
      </span>
    </div>
  )
}

function WorkspaceData({ stats, loading }) {
  const { t } = useTranslation()

  return (
    <section className="flex flex-col min-w-0" style={{ gap: 14 }}>
      <div style={{ color: 'var(--text-primary)', fontSize: 14, fontWeight: 700 }}>
        {t('chat.usage.workspaceData')}
      </div>
      {loading ? (
        <div className="skeleton" style={{ width: '100%', height: 60, borderRadius: 4 }} />
      ) : (
        <div
          className="grid min-w-0"
          style={{
            gridTemplateColumns: 'repeat(auto-fit, minmax(min(132px, 100%), 1fr))',
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 4,
            overflow: 'hidden',
          }}
        >
          <WorkspaceMetric label={t('userData.sessions')} value={stats?.session_count || 0} format={formatNumber} index={0} />
          <WorkspaceMetric label={t('userData.storage')} value={stats?.storage_bytes || 0} format={formatBytes} index={1} />
          <WorkspaceMetric label={t('userData.files')} value={stats?.file_count || 0} format={formatNumber} index={2} />
          <WorkspaceMetric label={t('userData.totalFileSize')} value={stats?.total_file_size || 0} format={formatBytes} index={3} />
          <WorkspaceMetric label={t('userData.lastActive')} value={relativeTime(stats?.last_active, t)} format={(value) => value} index={4} />
        </div>
      )}
    </section>
  )
}

function UsageStatusSkeleton() {
  return (
    <div className="flex flex-col" style={{ gap: 32 }}>
      <div className="flex flex-col items-center" style={{ gap: 8 }}>
        <div className="skeleton" style={{ width: 72, height: 72, borderRadius: 4 }} />
        <div className="skeleton" style={{ width: 132, height: 18 }} />
        <div className="skeleton" style={{ width: 96, height: 12 }} />
      </div>
      <div className="skeleton" style={{ width: '100%', height: 74, borderRadius: 4 }} />
      <div className="flex flex-col" style={{ gap: 12 }}>
        <div className="skeleton" style={{ width: 96, height: 14 }} />
        <div className="skeleton" style={{ width: '100%', height: 152, borderRadius: 4 }} />
      </div>
      <div className="grid gap-8" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(280px, 100%), 1fr))' }}>
        <div className="skeleton" style={{ height: 142, borderRadius: 4 }} />
        <div className="skeleton" style={{ height: 142, borderRadius: 4 }} />
      </div>
    </div>
  )
}

export function UsageStatsOverviewTitle() {
  const { t } = useTranslation()
  const user = useAuthStore((state) => state.user)

  if (!user) return null

  return (
    <div className="flex items-center gap-2 min-w-0">
      <Sparkles size={16} strokeWidth={1.5} style={{ color: 'var(--orange)', flexShrink: 0 }} />
      <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 16, fontWeight: 700 }}>
        {t('chat.usage.title', { name: user.username })}
      </span>
    </div>
  )
}

export default function UsageStatsOverview({ showTitle = true, workspaceStats, workspaceStatsLoading = false }) {
  const { t } = useTranslation()
  const user = useAuthStore((state) => state.user)
  const overview = useUserDataStore((state) => state.overview)
  const overviewLoading = useUserDataStore((state) => state.overviewLoading)
  const fetchOverview = useUserDataStore((state) => state.fetchOverview)
  const [statsTab, setStatsTab] = useState('overview')
  const [modelRange, setModelRange] = useState('all')
  const themeKey = useThemeKey()
  const statsPanelRef = useRef(null)
  const statsPanelAnimationRef = useRef(null)
  const hasRenderedStatsPanelRef = useRef(false)
  const reducedMotion = useReducedMotion()

  useEffect(() => { fetchOverview() }, [fetchOverview])

  useLayoutEffect(() => {
    const panel = statsPanelRef.current
    if (!panel || !hasRenderedStatsPanelRef.current) {
      hasRenderedStatsPanelRef.current = true
      return undefined
    }

    statsPanelAnimationRef.current?.cancel()
    if (reducedMotion) return undefined

    panel.style.willChange = 'opacity, transform'
    statsPanelAnimationRef.current = animate(panel, {
      opacity: [0, 1],
      translateY: [4, 0],
      duration: 200,
      ease: EASE_SPRING,
      onComplete: () => {
        panel.style.opacity = '1'
        panel.style.transform = ''
        panel.style.willChange = ''
      },
    })

    return () => {
      statsPanelAnimationRef.current?.cancel()
      panel.style.opacity = '1'
      panel.style.transform = ''
      panel.style.willChange = ''
    }
  }, [reducedMotion, statsTab])

  if (!user) return null

  if (overviewLoading && !overview) {
    return <UsageStatusSkeleton />
  }

  const data = overview || {}
  const all = data.stats?.all || {}
  const heatmap = data.heatmap || []
  const peakDailyTokens = heatmap.reduce((peak, cell) => Math.max(peak, Number(cell.count) || 0), 0)
  const metrics = [
    { label: t('chat.usage.card.tokens'), value: all.total_tokens || 0 },
    { label: t('chat.usage.card.peakDailyTokens'), value: peakDailyTokens },
    { label: t('chat.usage.card.messages'), value: all.messages || 0 },
    { label: t('chat.usage.card.currentStreak'), value: data.current_streak || 0, format: (value) => t('chat.usage.streakDays', { count: Math.round(value) }) },
    { label: t('chat.usage.card.longestStreak'), value: data.longest_streak || 0, format: (value) => t('chat.usage.streakDays', { count: Math.round(value) }) },
  ]
  const peakHour = data.peak_hour === null || data.peak_hour === undefined
    ? t('chat.usage.none')
    : `${data.peak_hour}${t('chat.usage.hourSuffix')}`
  const insights = [
    { label: t('chat.usage.card.activeDays'), value: formatNumber(all.active_days || 0) },
    { label: t('chat.usage.card.peakHour'), value: peakHour },
    { label: t('chat.usage.card.favoriteModel'), value: data.favorite_model || t('chat.usage.none') },
    { label: t('chat.usage.skills.explored'), value: formatNumber(data.explored_skills || 0) },
    { label: t('chat.usage.skills.totalUses'), value: formatNumber(data.skill_invocations || 0) },
  ]

  return (
    <div className="flex flex-col min-w-0" style={{ gap: showTitle ? 24 : 0 }}>
      {showTitle && <UsageStatsOverviewTitle />}
      <div className="flex flex-col min-w-0" style={{ gap: 36 }}>
        <section className="flex flex-col items-center" style={{ gap: 6 }}>
          <div
            className="flex items-center justify-center"
            style={{
              width: 56,
              height: 56,
              borderRadius: 4,
              background: 'var(--orange)',
              color: 'var(--text-inverse)',
              fontSize: 18,
              fontWeight: 400,
              lineHeight: 1,
            }}
          >
            {initials(user.username)}
          </div>
          <div style={{ color: 'var(--text-primary)', fontSize: 16, fontWeight: 700, lineHeight: 1.25 }}>
            {user.username}
          </div>
          <div style={{ color: 'var(--text-secondary)', fontSize: 11, lineHeight: 1.3 }}>
            @{user.username}
          </div>
        </section>

        <section
          className="grid min-w-0"
          style={{
            width: '60%',
            maxWidth: 504,
            margin: '0 auto',
            gridTemplateColumns: 'repeat(5, minmax(0, 1fr))',
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 4,
            overflow: 'hidden',
          }}
        >
          {metrics.map((metric, index) => <Metric key={metric.label} {...metric} index={index} />)}
        </section>

        <section className="flex flex-col min-w-0" style={{ gap: 14, width: '60%', maxWidth: 504, margin: '0 auto' }}>
          <div className="flex items-center justify-between gap-2">
            <Tabs
              tabs={[
                { id: 'overview', label: t('chat.usage.tab.overview') },
                { id: 'models', label: t('chat.usage.tab.models') },
              ]}
              activeKey={statsTab}
              onChange={(_, tab) => setStatsTab(tab.id)}
              variant="frame"
              className="flex items-center gap-1"
              indicatorStyle={{ border: 'none', borderRadius: 2 }}
              buttonStyle={{ padding: '3px 8px', borderRadius: 2, fontSize: 10 }}
              getButtonStyle={({ active }) => ({ fontWeight: active ? 600 : 400 })}
              layoutId="usage-stat-tab"
            />
            {statsTab === 'models' && (
              <Tabs
                tabs={[
                  { id: 'all', label: 'All' },
                  { id: '30d', label: '30d' },
                  { id: '7d', label: '7d' },
                ]}
                activeKey={modelRange}
                onChange={(_, tab) => setModelRange(tab.id)}
                variant="frame"
                className="flex items-center gap-1"
                indicatorStyle={{ border: 'none', borderRadius: 2 }}
                buttonStyle={{ padding: '3px 8px', borderRadius: 2, fontSize: 10 }}
                getButtonStyle={({ active }) => ({ fontWeight: active ? 600 : 400 })}
                layoutId="usage-model-range-tab"
              />
            )}
          </div>
          <div ref={statsPanelRef} className="min-w-0 overflow-y-auto" style={{ height: 304, paddingRight: 2 }}>
            {statsTab === 'overview' ? (
              <>
                <TokenHeatmap data={heatmap} />
                {data.tagline && (
                  <div style={{ color: 'var(--text-dim)', fontSize: 11, fontStyle: 'italic', lineHeight: 1.4, paddingLeft: 28 }}>
                    {data.tagline}
                  </div>
                )}
              </>
            ) : (
              <ModelsView
                modelUsage={data.model_usage || []}
                dailyModelTokens={data.daily_model_tokens || []}
                range={modelRange}
                themeKey={themeKey}
              />
            )}
          </div>
        </section>

        <section
          className="grid min-w-0"
          style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(280px, 100%), 1fr))', gap: 40 }}
        >
          <div className="flex flex-col min-w-0" style={{ gap: 14 }}>
            <div style={{ color: 'var(--text-primary)', fontSize: 14, fontWeight: 700 }}>
              {t('chat.usage.activityInsights')}
            </div>
            <div className="flex flex-col" style={{ gap: 10 }}>
              {insights.map((insight) => (
                <div key={insight.label} className="flex items-center justify-between gap-4 min-w-0">
                  <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{insight.label}</span>
                  <span
                    className="truncate"
                    style={{ color: 'var(--text-primary)', fontSize: 12, fontWeight: 600, textAlign: 'right' }}
                    title={insight.value}
                  >
                    {insight.value}
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="flex flex-col min-w-0" style={{ gap: 14 }}>
            <div style={{ color: 'var(--text-primary)', fontSize: 14, fontWeight: 700 }}>
              {t('chat.usage.skills.title')}
            </div>
            <SkillsList items={data.skill_usage} />
          </div>
        </section>

        <WorkspaceData stats={workspaceStats} loading={workspaceStatsLoading} />
      </div>
    </div>
  )
}
