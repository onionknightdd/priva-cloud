import { useState } from 'react'
import { Bot, Check, X, ChevronRight, ChevronLeft, Info } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { submitRegistration } from '../../api/auth'
import useOverlayTransition from '../../motion/useOverlayTransition'
import StepSlide from '../../motion/StepSlide'

const RUNNER_TYPES = ['auto_scale', 'persistent']
const MIN_CPU_CORES = 0.512
const MAX_CPU_CORES = 4
const MIN_MEMORY_MB = 1024
const MAX_MEMORY_MB = 4096
const MIN_VOLUME_GB = 1
const MAX_VOLUME_GB = 1024

function validateNumberField(value, { min, max, integer = false }) {
  if (value == null || String(value).trim() === '') return 'number'

  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) return 'number'
  if (integer && !Number.isInteger(numericValue)) return 'integer'
  if (numericValue < min || (max != null && numericValue > max)) return 'range'
  return null
}

function NumberField({ id, label, value, onChange, unit, min, max, step: stp, error, inputStyle, focusProps }) {
  return (
    <div className="flex items-center gap-3 min-w-0">
      <label
        htmlFor={id}
        className="text-xs uppercase flex-shrink-0"
        style={{ color: 'var(--text-secondary)', letterSpacing: '0.06em', fontWeight: 600, width: 64 }}
      >
        {label}
      </label>
      <input
        id={id}
        type="number"
        value={value}
        min={min}
        max={max}
        step={stp}
        aria-label={label}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(e) => onChange(e.target.value)}
        onFocus={(e) => {
          if (error) {
            e.target.style.borderColor = 'var(--red)'
            return
          }
          focusProps?.onFocus?.(e)
        }}
        onBlur={(e) => {
          if (error) {
            e.target.style.borderColor = 'var(--red)'
            return
          }
          focusProps?.onBlur?.(e)
        }}
        style={{ ...inputStyle, borderColor: error ? 'var(--red)' : undefined }}
      />
      <span className="text-xs font-light flex-shrink-0" style={{ color: 'var(--text-dim)' }}>
        {unit}
      </span>
      {error && (
        <span
          id={`${id}-error`}
          className="text-xs min-w-0"
          role="alert"
          style={{
            flex: '1 1 0',
            borderLeft: '2px solid var(--red)',
            color: 'var(--red)',
            fontWeight: 400,
            lineHeight: '16px',
            paddingLeft: 6,
            wordBreak: 'break-word',
            overflowWrap: 'break-word',
          }}
        >
          {error}
        </span>
      )}
    </div>
  )
}

function RegistrationWizardBody({ onClose, active, panelRef, backdropRef }) {
  const { t } = useTranslation()
  const [step, setStep] = useState(1)

  // Step 1 — account
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  // Step 2 — runner & resources
  const [runnerType, setRunnerType] = useState('auto_scale')
  const [cpuCores, setCpuCores] = useState('1')
  const [memoryMb, setMemoryMb] = useState('2048')
  const [volumeGb, setVolumeGb] = useState('1')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [submitted, setSubmitted] = useState(false)

  const accountValid =
    username.trim().length > 0 &&
    password.length >= 8 &&
    confirmPassword === password

  const formatResourceError = (type, rangeKey) => {
    if (!type) return ''
    if (type === 'number') return t('auth.regNumberInvalid')
    if (type === 'integer') return t('auth.regIntegerRequired')
    return t(rangeKey)
  }
  const cpuError = formatResourceError(
    validateNumberField(cpuCores, { min: MIN_CPU_CORES, max: MAX_CPU_CORES }),
    'auth.regCpuRange'
  )
  const memoryError = formatResourceError(
    validateNumberField(memoryMb, { min: MIN_MEMORY_MB, max: MAX_MEMORY_MB, integer: true }),
    'auth.regMemoryRange'
  )
  const volumeError = formatResourceError(
    validateNumberField(volumeGb, { min: MIN_VOLUME_GB, max: MAX_VOLUME_GB, integer: true }),
    'auth.regVolumeRange'
  )
  const resourceValid = !cpuError && !memoryError && !volumeError

  const handleSubmit = async () => {
    if (!resourceValid) return

    setSubmitting(true)
    setError('')
    try {
      const payload = {
        username: username.trim(),
        password,
        runner_type: runnerType,
        cpu_cores: Number(cpuCores),
        memory_mb: Number(memoryMb),
        volume_gb: Number(volumeGb),
      }
      const dn = displayName.trim()
      if (dn) payload.display_name = dn
      await submitRegistration(payload)
      setSubmitted(true)
    } catch (err) {
      setError(err.message || t('auth.loginFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  const inputStyle = {
    width: '100%',
    padding: '8px 12px',
    background: 'var(--bg-elevated)',
    border: '1px solid var(--border)',
    borderRadius: 4,
    color: 'var(--text-primary)',
    fontSize: 13,
    fontFamily: 'var(--font-ui)',
    outline: 'none',
    boxSizing: 'border-box',
    transition: 'border-color 150ms ease',
  }

  const numberInputStyle = {
    ...inputStyle,
    width: 96,
    fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
  }

  const focusProps = {
    onFocus: (e) => { e.target.style.borderColor = 'var(--blue)' },
    onBlur: (e) => { e.target.style.borderColor = 'var(--border)' },
  }

  const labelStyle = {
    display: 'block',
    marginBottom: 4,
    color: 'var(--text-secondary)',
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
  }

  const STEPS = [
    { n: 1, label: t('auth.regStepAccount') },
    { n: 2, label: t('auth.regStepRunner') },
    { n: 3, label: t('auth.regStepReview') },
  ]

  const RunnerCard = ({ type, desc, selected, onSelect }) => (
    <button
      type="button"
      className="flex items-start gap-3 px-3 py-3 text-left flex-1 min-w-0"
      style={{
        background: selected ? 'var(--bg-elevated)' : 'transparent',
        border: '1px solid var(--border)',
        borderLeft: `2px solid ${selected ? 'var(--blue)' : 'var(--border)'}`,
        borderRadius: 4,
        cursor: 'pointer',
        transition: 'background 150ms ease, border-color 150ms ease',
      }}
      onClick={onSelect}
    >
      <span
        className="flex items-center justify-center flex-shrink-0"
        style={{
          width: 16,
          height: 16,
          borderRadius: '50%',
          border: `1px solid ${selected ? 'var(--blue)' : 'var(--border-strong)'}`,
          marginTop: 1,
        }}
      >
        {selected && (
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--blue)' }} />
        )}
      </span>
      <span className="flex flex-col gap-1 min-w-0">
        <span
          className="text-sm"
          style={{
            color: 'var(--text-primary)',
            fontWeight: 600,
            fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
          }}
        >
          {type}
        </span>
        <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
          {desc}
        </span>
      </span>
    </button>
  )

  const ReviewRow = ({ label, value }) => (
    <div className="flex items-center gap-2">
      <span className="text-xs flex-shrink-0" style={{ color: 'var(--text-dim)', width: 110, fontWeight: 600 }}>
        {label}
      </span>
      <span
        className="text-xs truncate"
        style={{ color: 'var(--text-primary)', fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace" }}
      >
        {value || '—'}
      </span>
    </div>
  )

  const backBtn = (onClick) => (
    <button
      type="button"
      className="flex items-center gap-1 px-4 py-2 text-xs"
      style={{
        background: 'transparent',
        border: '1px solid var(--border)',
        borderRadius: 4,
        color: 'var(--text-secondary)',
        cursor: 'pointer',
        transition: 'border-color 150ms ease',
      }}
      onClick={onClick}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)' }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
    >
      <ChevronLeft size={12} strokeWidth={1.5} /> {t('auth.back')}
    </button>
  )

  const nextBtn = (onClick, disabled) => (
    <button
      type="button"
      className="flex items-center gap-1 px-4 py-2 text-xs font-semibold"
      disabled={disabled}
      style={{
        background: disabled ? 'var(--bg-elevated)' : 'var(--blue)',
        color: disabled ? 'var(--text-dim)' : 'var(--text-inverse)',
        border: 'none',
        borderRadius: 4,
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'opacity 150ms ease',
      }}
      onClick={onClick}
    >
      {t('auth.next')} <ChevronRight size={12} strokeWidth={1.5} />
    </button>
  )

  return (
    <div
      ref={backdropRef}
      className="fixed inset-0 flex items-center justify-center"
      style={{
        background: 'var(--bg-overlay)',
        backdropFilter: 'blur(4px)',
        zIndex: 200,
        pointerEvents: active ? 'auto' : 'none',
      }}
      onClick={onClose}
    >
      <div
        ref={panelRef}
        style={{
          width: 480,
          maxWidth: '90%',
          maxHeight: '80vh',
          overflowY: 'auto',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: 4,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2 px-6 pt-6 pb-2">
          <Bot size={20} strokeWidth={1.5} style={{ color: 'var(--blue)' }} />
          <span className="font-bold" style={{ color: 'var(--text-primary)', fontSize: 16 }}>
            {t('auth.createAccount')}
          </span>
          <div className="flex-1" />
          <button
            type="button"
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--text-dim)', transition: 'color 150ms ease' }}
            onClick={onClose}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
          >
            <X size={14} strokeWidth={1.5} />
          </button>
        </div>

        {/* Step indicator */}
        {!submitted && (
          <div className="flex items-center px-6 pb-4">
            {STEPS.map(({ n, label }, i) => {
              const done = n < step
              const active = n === step
              return (
                <div key={n} className="flex items-center" style={{ flex: i < STEPS.length - 1 ? 1 : '0 0 auto', minWidth: 0 }}>
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className="flex items-center justify-center flex-shrink-0 text-xs font-semibold"
                      style={{
                        width: 22,
                        height: 22,
                        borderRadius: '50%',
                        border: `1px solid ${active || done ? 'var(--blue)' : 'var(--border-strong)'}`,
                        background: done ? 'var(--blue)' : 'transparent',
                        color: done ? 'var(--text-inverse)' : active ? 'var(--blue)' : 'var(--text-dim)',
                      }}
                    >
                      {done ? <Check size={12} strokeWidth={1.5} /> : n}
                    </span>
                    <span
                      className="text-xs truncate"
                      style={{
                        color: active ? 'var(--text-primary)' : 'var(--text-dim)',
                        fontWeight: active ? 600 : 400,
                      }}
                    >
                      {label}
                    </span>
                  </div>
                  {i < STEPS.length - 1 && (
                    <div
                      className="flex-1"
                      style={{ height: 1, margin: '0 8px', background: done ? 'var(--blue)' : 'var(--border)' }}
                    />
                  )}
                </div>
              )
            })}
          </div>
        )}

        <div className="px-6 pb-6">
          {/* Success state */}
          {submitted ? (
            <div className="flex flex-col items-center gap-4 py-6">
              <span
                className="flex items-center justify-center"
                style={{ width: 40, height: 40, borderRadius: '50%', border: '1px solid var(--green)' }}
              >
                <Check size={20} strokeWidth={1.5} style={{ color: 'var(--green)' }} />
              </span>
              <p className="text-sm" style={{ color: 'var(--text-primary)', textAlign: 'center', margin: 0 }}>
                {t('auth.regSuccessTitle')}
              </p>
              <button
                type="button"
                className="px-4 py-2 text-xs font-semibold"
                style={{
                  background: 'var(--blue)',
                  color: 'var(--text-inverse)',
                  border: 'none',
                  borderRadius: 4,
                  cursor: 'pointer',
                  transition: 'opacity 150ms ease',
                }}
                onClick={onClose}
                onMouseEnter={(e) => { e.currentTarget.style.opacity = '0.85' }}
                onMouseLeave={(e) => { e.currentTarget.style.opacity = '1' }}
              >
                {t('auth.regBackToLogin')}
              </button>
            </div>
          ) : (
            <StepSlide stepKey={step}>
              {/* Step 1 — Account */}
              {step === 1 && (
                <div className="flex flex-col gap-4">
                  <div>
                    <label style={labelStyle}>{t('admin.username')}</label>
                    <input
                      type="text"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      style={inputStyle}
                      autoComplete="username"
                      autoFocus
                      {...focusProps}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>{t('auth.displayNameOptional')}</label>
                    <input
                      type="text"
                      value={displayName}
                      onChange={(e) => setDisplayName(e.target.value)}
                      style={inputStyle}
                      {...focusProps}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>{t('auth.password')}</label>
                    <input
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      style={inputStyle}
                      autoComplete="new-password"
                      {...focusProps}
                    />
                    {password.length > 0 && password.length < 8 && (
                      <span className="text-xs" style={{ color: 'var(--yellow)', display: 'block', marginTop: 4 }}>
                        {t('auth.passwordMinLength')}
                      </span>
                    )}
                  </div>
                  <div>
                    <label style={labelStyle}>{t('auth.confirmPassword')}</label>
                    <input
                      type="password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      style={inputStyle}
                      autoComplete="new-password"
                      {...focusProps}
                    />
                    {confirmPassword.length > 0 && confirmPassword !== password && (
                      <span className="text-xs" style={{ color: 'var(--yellow)', display: 'block', marginTop: 4 }}>
                        {t('auth.passwordMismatch')}
                      </span>
                    )}
                  </div>

                  <div className="flex items-center justify-end pt-2">
                    {nextBtn(() => setStep(2), !accountValid)}
                  </div>
                </div>
              )}

              {/* Step 2 — Runner & Resources */}
              {step === 2 && (
                <div className="flex flex-col gap-4">
                  <div className="flex flex-col gap-2">
                    <span style={labelStyle}>{t('auth.regRunnerType')}</span>
                    <div className="flex gap-2">
                      <RunnerCard
                        type={t('auth.regRunnerAutoScale')}
                        desc={t('auth.regRunnerAutoScaleDesc')}
                        selected={runnerType === 'auto_scale'}
                        onSelect={() => setRunnerType('auto_scale')}
                      />
                      <RunnerCard
                        type={t('auth.regRunnerPersistent')}
                        desc={t('auth.regRunnerPersistentDesc')}
                        selected={runnerType === 'persistent'}
                        onSelect={() => setRunnerType('persistent')}
                      />
                    </div>
                  </div>

                  <div className="flex flex-col gap-3">
                    <span style={labelStyle}>{t('auth.regResourceRequest')}</span>
                    <NumberField
                      id="registration-cpu"
                      label={t('auth.regCpu')}
                      value={cpuCores}
                      onChange={setCpuCores}
                      unit={t('auth.regCpuUnit')}
                      min={MIN_CPU_CORES}
                      max={MAX_CPU_CORES}
                      step={0.001}
                      error={cpuError}
                      inputStyle={numberInputStyle}
                      focusProps={focusProps}
                    />
                    <NumberField
                      id="registration-memory"
                      label={t('auth.regMemory')}
                      value={memoryMb}
                      onChange={setMemoryMb}
                      unit={t('auth.regMemoryUnit')}
                      min={MIN_MEMORY_MB}
                      max={MAX_MEMORY_MB}
                      step={256}
                      error={memoryError}
                      inputStyle={numberInputStyle}
                      focusProps={focusProps}
                    />
                    <NumberField
                      id="registration-volume"
                      label={t('auth.regVolume')}
                      value={volumeGb}
                      onChange={setVolumeGb}
                      unit={t('auth.regVolumeUnit')}
                      min={MIN_VOLUME_GB}
                      max={MAX_VOLUME_GB}
                      step={1}
                      error={volumeError}
                      inputStyle={numberInputStyle}
                      focusProps={focusProps}
                    />
                  </div>

                  <div className="flex items-center justify-between pt-2">
                    {backBtn(() => setStep(1))}
                    {nextBtn(() => setStep(3), !resourceValid)}
                  </div>
                </div>
              )}

              {/* Step 3 — Review */}
              {step === 3 && (
                <div className="flex flex-col gap-4">
                  <div
                    className="flex flex-col gap-2 px-3 py-3"
                    style={{ background: 'var(--bg-elevated)', borderRadius: 4 }}
                  >
                    <ReviewRow label={t('admin.username')} value={username} />
                    <ReviewRow label={t('auth.displayName')} value={displayName} />
                    <ReviewRow label={t('auth.regRunnerType')} value={runnerType} />
                    <ReviewRow label={t('auth.regCpu')} value={`${cpuCores} ${t('auth.regCpuUnit')}`} />
                    <ReviewRow label={t('auth.regMemory')} value={`${memoryMb} ${t('auth.regMemoryUnit')}`} />
                    <ReviewRow label={t('auth.regVolume')} value={`${volumeGb} ${t('auth.regVolumeUnit')}`} />
                  </div>

                  <div className="flex items-start gap-2">
                    <Info size={12} strokeWidth={1.5} style={{ color: 'var(--cyan)', flexShrink: 0, marginTop: 2 }} />
                    <span className="text-xs font-light" style={{ color: 'var(--text-secondary)' }}>
                      {t('auth.regReviewInfo')}
                    </span>
                  </div>

                  {error && (
                    <div className="text-xs" style={{ color: 'var(--red)' }}>{error}</div>
                  )}

                  <div className="flex items-center justify-between pt-2">
                    {backBtn(() => setStep(2))}
                    <button
                      type="button"
                      className="px-4 py-2 text-xs font-semibold"
                      disabled={submitting || !resourceValid}
                      style={{
                        background: 'var(--green)',
                        color: 'var(--text-inverse)',
                        border: 'none',
                        borderRadius: 4,
                        cursor: submitting || !resourceValid ? 'default' : 'pointer',
                        opacity: submitting || !resourceValid ? 0.6 : 1,
                        transition: 'opacity 150ms ease',
                      }}
                      onClick={handleSubmit}
                    >
                      {submitting ? t('auth.regSubmitting') : t('auth.regSubmit')}
                    </button>
                  </div>
                </div>
              )}
            </StepSlide>
          )}
        </div>
      </div>
    </div>
  )
}

// Shell: owns the enter/exit envelope. The body (and all its wizard state)
// mounts fresh per open and unmounts only after the exit animation completes.
export default function RegistrationWizardModal({ open = true, onClose }) {
  const { mounted, panelRef, backdropRef } = useOverlayTransition({ open, variant: 'scale' })
  if (!mounted) return null
  return (
    <RegistrationWizardBody
      active={open}
      panelRef={panelRef}
      backdropRef={backdropRef}
      onClose={onClose}
    />
  )
}
