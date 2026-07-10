import { useTranslation } from 'react-i18next'
import useHooksStore from '../../stores/hooksStore'
import { HOOK_DEFINITIONS, HOOK_GROUPS, PHASE_COLORS } from '../../data/hookDefinitions'
import Chip from '@shared/components/shared/Chip'

const hookMap = Object.fromEntries(HOOK_DEFINITIONS.map(h => [h.id, h]))

// Hook-event list column for the content-only Hooks view — events grouped by
// lifecycle phase (session / tool / agent / misc).
export default function HooksSidebar() {
  const { t } = useTranslation()
  const selectedHookId = useHooksStore((s) => s.selectedHookId)
  const selectHook = useHooksStore((s) => s.selectHook)
  const configuredHooks = useHooksStore((s) => s.configuredHooks)

  return (
    <div className="flex-1 overflow-y-auto py-1">
      {HOOK_GROUPS.map((group) => (
        <div key={group.id}>
          {/* Group header */}
          <div
            className="px-3 py-2 uppercase font-semibold"
            style={{
              fontSize: 11,
              color: 'var(--text-dim)',
              letterSpacing: '0.08em',
            }}
          >
            {t(group.labelKey)}
          </div>

          {/* Group description */}
          <div
            className="px-3"
            style={{
              fontSize: 11,
              color: 'var(--text-dim)',
              paddingBottom: 6,
              lineHeight: 1.4,
            }}
          >
            {t(group.descKey)}
          </div>

          {/* Hook items — indented under group */}
          {group.hookIds.map((hookId) => {
            const hook = hookMap[hookId]
            if (!hook) return null
            const isActive = selectedHookId === hookId
            const handlerCount = configuredHooks[hookId]?.handlers?.length || 0

            return (
              <button
                key={hookId}
                className="flex items-center w-full"
                style={{
                  height: 36,
                  background: isActive ? 'var(--bg-elevated)' : 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontSize: 13,
                  textAlign: 'left',
                  transition: 'background 150ms ease, color 150ms ease',
                  paddingLeft: 16,
                  paddingRight: 12,
                }}
                onClick={() => selectHook(hookId)}
                onMouseEnter={(e) => {
                  if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)'
                }}
                onMouseLeave={(e) => {
                  if (!isActive) e.currentTarget.style.background = 'transparent'
                }}
              >
                {/* Status border indicator — adjacent to text */}
                <span
                  className="flex-shrink-0"
                  style={{
                    width: 2,
                    height: 20,
                    borderRadius: 1,
                    background: isActive ? 'var(--blue)' : PHASE_COLORS[hook.phase],
                    marginRight: 8,
                  }}
                />
                <span className="flex-1 truncate" style={{ minWidth: 0 }}>{hook.id}</span>
                <span className="flex items-center gap-1 flex-shrink-0">
                  {hook.canBlock && <Chip color="var(--red)">{t('hooks.block')}</Chip>}
                  {handlerCount > 0 && (
                    <span
                      style={{
                        fontSize: 11,
                        color: 'var(--text-dim)',
                        background: 'var(--bg-elevated)',
                        padding: '1px 5px',
                        borderRadius: 3,
                      }}
                    >
                      {handlerCount}
                    </span>
                  )}
                </span>
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}
