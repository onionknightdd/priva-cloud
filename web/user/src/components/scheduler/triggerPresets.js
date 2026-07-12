// Preset ⇄ trigger mapping for the drawer (design §9.2) + the human paraphrase.
//
// Cron day-of-week: APScheduler's crontab parser reads NUMERIC days with
// 0=Monday (not the crontab-manpage 0=Sunday), so every preset emits NAMED
// days (mon, mon-fri) — unambiguous to both the parser and the reader.

export const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

export const PRESETS = ['daily', 'weekdays', 'weekly', 'everyNHours', 'everyNMinutes', 'custom']

const TIME_RE = /^([01]?\d|2[0-3]):([0-5]\d)$/

export function parseTime(text) {
  const m = TIME_RE.exec((text || '').trim())
  if (!m) return null
  return { hour: Number(m[1]), minute: Number(m[2]) }
}

const pad = (n) => String(n).padStart(2, '0')

// preset + params → TriggerConfig (the wire shape)
export function presetToTrigger(preset, { time = '09:00', weekday = 'mon', n = 1 } = {}) {
  const tm = parseTime(time) || { hour: 9, minute: 0 }
  switch (preset) {
    case 'daily':
      return { type: 'cron', expr: `${tm.minute} ${tm.hour} * * *` }
    case 'weekdays':
      return { type: 'cron', expr: `${tm.minute} ${tm.hour} * * mon-fri` }
    case 'weekly':
      return { type: 'cron', expr: `${tm.minute} ${tm.hour} * * ${weekday}` }
    case 'everyNHours':
      return { type: 'interval', hours: Math.max(1, Math.floor(n)) }
    case 'everyNMinutes':
      return { type: 'interval', minutes: Math.max(1, Math.floor(n)) }
    default:
      return null // custom: the raw expr field owns the value
  }
}

// Reverse-map a stored trigger back onto a preset for edit mode; falls back to
// custom (cron) / everyN (interval) so every stored job lands somewhere sane.
export function triggerToPreset(trigger) {
  if (!trigger) return { preset: 'daily', time: '09:00', weekday: 'mon', n: 1 }
  if (trigger.type === 'interval') {
    const { weeks = 0, days = 0, hours = 0, minutes = 0, seconds = 0 } = trigger
    if (!weeks && !days && !seconds && hours && !minutes) {
      return { preset: 'everyNHours', n: hours, time: '09:00', weekday: 'mon' }
    }
    if (!weeks && !days && !seconds && !hours && minutes) {
      return { preset: 'everyNMinutes', n: minutes, time: '09:00', weekday: 'mon' }
    }
    // mixed interval: closest editable form is minutes
    const total = ((weeks * 7 + days) * 24 + hours) * 60 + minutes + Math.round(seconds / 60)
    return { preset: 'everyNMinutes', n: Math.max(1, total), time: '09:00', weekday: 'mon' }
  }
  const m = /^(\d{1,2}) (\d{1,2}) \* \* (\S+)$/.exec(trigger.expr || '')
  if (m) {
    const time = `${pad(m[2])}:${pad(m[1])}`
    const dow = m[3].toLowerCase()
    if (dow === '*') return { preset: 'daily', time, weekday: 'mon', n: 1 }
    if (dow === 'mon-fri') return { preset: 'weekdays', time, weekday: 'mon', n: 1 }
    if (WEEKDAYS.includes(dow)) return { preset: 'weekly', time, weekday: dow, n: 1 }
  }
  return { preset: 'custom', expr: trigger.expr || '', time: '09:00', weekday: 'mon', n: 1 }
}

// Dim human paraphrase shown next to the mono trigger line + drawer preview.
export function describeTrigger(trigger, t) {
  if (!trigger) return ''
  if (trigger.type === 'interval') {
    const parts = []
    if (trigger.weeks) parts.push(`${trigger.weeks}w`)
    if (trigger.days) parts.push(`${trigger.days}d`)
    if (trigger.hours) parts.push(`${trigger.hours}h`)
    if (trigger.minutes) parts.push(`${trigger.minutes}m`)
    if (trigger.seconds) parts.push(`${trigger.seconds}s`)
    return t('scheduler.every', { defaultValue: 'every' }) + ' ' + (parts.join(' ') || '—')
  }
  const p = triggerToPreset(trigger)
  const dayName = (d) => t(`scheduler.days_${d}`, { defaultValue: d })
  switch (p.preset) {
    case 'daily':
      return t('scheduler.presetDailyAt', { time: p.time, defaultValue: `daily at ${p.time}` })
    case 'weekdays':
      return t('scheduler.presetWeekdaysAt', { time: p.time, defaultValue: `Mon–Fri at ${p.time}` })
    case 'weekly':
      return t('scheduler.presetWeeklyAt', {
        day: dayName(p.weekday), time: p.time,
        defaultValue: `${dayName(p.weekday)} at ${p.time}`,
      })
    default:
      return trigger.expr || ''
  }
}

// Mono one-liner of the raw trigger (jobs list second line / detail header).
export function formatTrigger(trigger) {
  if (!trigger) return ''
  if (trigger.type === 'interval') {
    const parts = []
    if (trigger.weeks) parts.push(`${trigger.weeks}w`)
    if (trigger.days) parts.push(`${trigger.days}d`)
    if (trigger.hours) parts.push(`${trigger.hours}h`)
    if (trigger.minutes) parts.push(`${trigger.minutes}m`)
    if (trigger.seconds) parts.push(`${trigger.seconds}s`)
    return `every ${parts.join(' ') || '—'}`
  }
  return trigger.expr || ''
}
