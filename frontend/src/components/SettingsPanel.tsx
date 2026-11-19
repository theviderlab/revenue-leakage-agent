import { useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../lib/api'
import { getSettings, putSettings } from '../lib/client'
import { formatValue, label } from '../lib/format'
import type { SettingInfo, SettingsResponse, SettingsUpdate } from '../lib/types'
import { Badge } from './Badge'

const HELP: Record<SettingInfo['key'], string> = {
  as_of: 'Cutoff date of the analysis. Not set means today.',
  data_up_to_date: 'Whether the data covers everything up to the cutoff.',
  period_calendar: 'Billing periods anchored to the plan start date, or calendar periods.',
  amendment_proration: 'How the period of an amendment is split between the two plans.',
  auto_renewal: 'Whether a plan renews for another 12 months when its term ends.',
}

// Form values are strings; '' means "not fixed" (null in the API, the default applies).
function toText(value: SettingInfo['value']): string {
  return value === null ? '' : String(value)
}

function fromText(setting: SettingInfo, text: string): string | boolean | null {
  if (text === '') return null
  const match = setting.valid_values?.find((v) => String(v) === text)
  return match ?? text
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return 'There is a pending action: decide it in the chat first.'
    if (err.status === 422) return 'That value is not valid (the cutoff cannot be in the future).'
    return `The server rejected the change (${err.status}).`
  }
  return 'Could not reach the server.'
}

function today(): string {
  const d = new Date()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${month}-${day}`
}

export function SettingsPanel({ onSaved }: { onSaved: () => void }) {
  const [settings, setSettings] = useState<SettingsResponse | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  function adopt(response: SettingsResponse) {
    setSettings(response)
    setDraft(Object.fromEntries(response.settings.map((s) => [s.key, toText(s.value)])))
  }

  useEffect(() => {
    getSettings()
      .then(adopt)
      .catch(() => setError('Could not load the settings.'))
  }, [])

  if (!settings) return <p className="text-sm text-slate-500">{error ?? 'Loading settings...'}</p>

  const changed = settings.settings.filter((s) => draft[s.key] !== toText(s.value))

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!settings) return
    const update: Record<string, string | boolean | null> = {}
    for (const s of changed) update[s.key] = fromText(s, draft[s.key])
    setSaving(true)
    setError(null)
    try {
      adopt(await putSettings(update as SettingsUpdate))
      onSaved()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={save} className="space-y-3">
      <p className="text-xs text-slate-500">
        Global assumptions of the analysis. Data available through {settings.data_through}. Settings
        version {settings.version}. Changing them makes earlier findings stale.
      </p>
      {settings.settings.map((s) => {
        const fixed = s.value !== null
        const id = `setting-${s.key}`
        return (
          <div key={s.key} className="space-y-1">
            <div className="flex items-center gap-2">
              <label htmlFor={id} className="text-sm font-medium">
                {label(s.key)}
              </label>
              <Badge tone={fixed ? 'good' : 'neutral'}>{fixed ? 'fixed' : 'not fixed'}</Badge>
            </div>
            {s.key === 'as_of' ? (
              <input
                id={id}
                type="date"
                max={today()}
                value={draft[s.key] ?? ''}
                onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })}
                className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              />
            ) : (
              <select
                id={id}
                value={draft[s.key] ?? ''}
                onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })}
                className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="">
                  {s.default === null ? 'Not set' : `Default (${formatValue(s.default)})`}
                </option>
                {s.valid_values?.map((v) => (
                  <option key={String(v)} value={String(v)}>
                    {formatValue(v)}
                  </option>
                ))}
              </select>
            )}
            <p className="text-xs text-slate-500">
              {HELP[s.key]} Default: {s.default === null ? 'not set' : formatValue(s.default)}.
            </p>
          </div>
        )
      })}
      {error && (
        <p role="alert" className="rounded-md bg-red-50 px-2 py-1 text-sm text-red-800">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={saving || changed.length === 0}
        className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
      >
        Save settings
      </button>
    </form>
  )
}
