import { ArrowDown, ArrowUp, Check, EyeOff, Loader2, Pencil, RotateCcw, Search, Settings2, X } from 'lucide-react'
import { type KeyboardEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { api, type PlacementChanges, type SettingsService } from '../api'
import { StatusDot } from '../components/StatusDot'
import { useApp } from '../state'

type Filter = 'all' | 'edited' | 'hidden' | 'discovered'

/** A text field that saves on Enter or when it loses focus, and shows the default when empty. */
function InlineField({
  value,
  placeholder,
  list,
  onSave,
  label,
  className = '',
}: {
  value: string
  placeholder: string
  list?: string
  onSave: (value: string) => Promise<void>
  label: string
  className?: string
}) {
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  useEffect(() => setDraft(value), [value])
  const commit = async () => {
    if (draft.trim() === value.trim()) return
    setSaving(true)
    try {
      await onSave(draft.trim())
    } finally {
      setSaving(false)
    }
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') e.currentTarget.blur()
    if (e.key === 'Escape') {
      setDraft(value)
      e.currentTarget.blur()
    }
  }
  return (
    <span className={`inline-field ${className}${saving ? ' saving' : ''}`}>
      <input
        value={draft}
        placeholder={placeholder}
        list={list}
        aria-label={label}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => void commit()}
        onKeyDown={onKey}
      />
      {saving ? <Loader2 size={12} className="spin" /> : <Pencil size={11} className="inline-pen" />}
    </span>
  )
}

export function Settings() {
  const { route, reload } = useApp()
  const [services, setServices] = useState<SettingsService[] | null>(null)
  const [groups, setGroups] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [query, setQuery] = useState(route.drawer?.kind === 'focus' ? route.drawer.id : '')
  const [filter, setFilter] = useState<Filter>('all')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [target, setTarget] = useState('')
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameTo, setRenameTo] = useState('')
  const [busy, setBusy] = useState(false)
  const focus = route.drawer?.kind === 'focus' ? route.drawer.id : null

  const load = useCallback(async () => {
    try {
      const body = await api.settingsServices()
      setServices(body.services)
      setGroups(body.groups)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (!note) return
    const id = window.setTimeout(() => setNote(null), 3000)
    return () => window.clearTimeout(id)
  }, [note])

  /** Run a change, then reload this list and the page's snapshot. */
  const apply = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true)
    try {
      await work()
      setNote(done)
      await load()
      reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const change = (ids: string[], changes: PlacementChanges, done: string) =>
    apply(() => api.changeServices(ids, changes), done)

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (services ?? []).filter((s) => {
      if (filter === 'edited' && !s.override) return false
      if (filter === 'hidden' && !s.hidden) return false
      if (filter === 'discovered' && !s.discovered) return false
      if (!q) return true
      return [s.id, s.name, s.group, s.stack ?? '', ...s.containers.map((c) => c.name)].some((t) =>
        t.toLowerCase().includes(q),
      )
    })
  }, [services, query, filter])

  const byGroup = useMemo(() => {
    const order = [...groups, ...new Set(visible.map((s) => s.group).filter((g) => !groups.includes(g)))]
    return order
      .map((group) => ({ group, items: visible.filter((s) => s.group === group) }))
      .filter((g) => g.items.length)
  }, [groups, visible])

  const counts = useMemo(() => {
    const all = services ?? []
    return {
      all: all.length,
      edited: all.filter((s) => s.override).length,
      hidden: all.filter((s) => s.hidden).length,
      discovered: all.filter((s) => s.discovered).length,
    }
  }, [services])

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const toggleGroup = (ids: string[]) =>
    setSelected((prev) => {
      const next = new Set(prev)
      const all = ids.every((id) => next.has(id))
      ids.forEach((id) => (all ? next.delete(id) : next.add(id)))
      return next
    })

  const ids = [...selected]
  const groupCounts = (services ?? []).reduce<Record<string, number>>((acc, s) => {
    if (!s.hidden) acc[s.group] = (acc[s.group] ?? 0) + 1
    return acc
  }, {})
  const orderedGroups = groups.filter((g) => groupCounts[g])

  const move = (index: number, delta: number) => {
    const next = [...orderedGroups]
    const [item] = next.splice(index, 1)
    next.splice(index + delta, 0, item)
    void apply(() => api.orderGroups(next), `Moved ${item} ${delta < 0 ? 'up' : 'down'}`)
  }

  if (!services) {
    return (
      <div className="settings">
        {error ? (
          <p className="error">Settings unavailable: {error}</p>
        ) : (
          <p className="muted">
            <Loader2 size={14} className="spin" /> Loading settings…
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="settings">
      <div className="settings-head">
        <h2>
          <Settings2 size={18} /> Settings
        </h2>
        <p className="small muted">
          Changes apply at once and survive restarts. They are kept in Executor's database; config.yaml and compose
          labels stay as the defaults, and Reset brings those back.
        </p>
      </div>
      {error && (
        <p className="error">
          {error}{' '}
          <button type="button" className="link-btn" onClick={() => setError(null)}>
            dismiss
          </button>
        </p>
      )}
      {note && (
        <p className="settings-note" role="status">
          <Check size={14} /> {note}
        </p>
      )}

      <section className="card settings-card">
        <div className="settings-card-head">
          <h3>Services</h3>
          <div className="toolbar">
            <label className="search">
              <Search size={14} aria-hidden="true" />
              <input
                type="search"
                placeholder="Find a service, stack or container"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Find a service"
              />
            </label>
            <div className="range-tabs" role="tablist" aria-label="Show">
              {(['all', 'edited', 'hidden', 'discovered'] as Filter[]).map((f) => (
                <button
                  key={f}
                  type="button"
                  role="tab"
                  aria-selected={filter === f}
                  className={filter === f ? 'active' : ''}
                  onClick={() => setFilter(f)}
                >
                  {f[0].toUpperCase() + f.slice(1)} <span className="num muted">{counts[f]}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        <datalist id="settings-groups">
          {groups.map((g) => (
            <option key={g} value={g} />
          ))}
        </datalist>

        <div className={`bulk-bar${ids.length ? ' bulk-on' : ''}`}>
          <span className="small">
            {ids.length ? `${ids.length} selected` : 'Select services to change several at once'}
          </span>
          {ids.length > 0 && (
            <>
              <span className="bulk-move">
                <input
                  list="settings-groups"
                  placeholder="Group (pick or type a new one)"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  aria-label="Group to move the selected services to"
                />
                <button
                  type="button"
                  className="btn btn-low btn-small"
                  disabled={!target.trim() || busy}
                  onClick={() =>
                    void change(ids, { group: target.trim() }, `Moved ${ids.length} to ${target.trim()}`).then(() => {
                      setSelected(new Set())
                      setTarget('')
                    })
                  }
                >
                  Move
                </button>
              </span>
              <button
                type="button"
                className="btn btn-ghost btn-small"
                disabled={busy}
                onClick={() => void change(ids, { hidden: true }, `Hid ${ids.length}`).then(() => setSelected(new Set()))}
              >
                <EyeOff size={13} /> Hide
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-small"
                disabled={busy}
                onClick={() => void change(ids, { hidden: false }, `Showing ${ids.length}`).then(() => setSelected(new Set()))}
              >
                Show
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-small"
                disabled={busy}
                onClick={() =>
                  void apply(() => api.changeServices(ids, null, true), `Reset ${ids.length} to defaults`).then(() =>
                    setSelected(new Set()),
                  )
                }
              >
                <RotateCcw size={13} /> Reset
              </button>
              <button type="button" className="icon-btn" onClick={() => setSelected(new Set())} aria-label="Clear selection">
                <X size={14} />
              </button>
            </>
          )}
        </div>

        {byGroup.map(({ group, items }) => {
          const groupIds = items.map((s) => s.id)
          const all = groupIds.every((id) => selected.has(id))
          return (
            <div key={group} className="settings-group">
              <label className="settings-group-head">
                <input type="checkbox" checked={all} onChange={() => toggleGroup(groupIds)} />
                <span>{group}</span>
                <span className="small muted num">{items.length}</span>
              </label>
              <ul className="settings-rows">
                {items.map((s) => (
                  <li
                    key={s.id}
                    className={`settings-row${s.hidden ? ' row-hidden' : ''}${focus === s.id ? ' row-focus' : ''}${selected.has(s.id) ? ' row-selected' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(s.id)}
                      onChange={() => toggle(s.id)}
                      aria-label={`Select ${s.name}`}
                    />
                    <StatusDot status={s.status} />
                    <InlineField
                      className="field-name"
                      value={s.override?.name ?? ''}
                      placeholder={s.defaults?.name ?? s.name}
                      label={`Name of ${s.name}`}
                      onSave={(v) => change([s.id], { name: v || null }, v ? `Renamed to ${v}` : `${s.defaults?.name} keeps its own name`)}
                    />
                    <InlineField
                      className="field-group"
                      value={s.override?.group ?? ''}
                      placeholder={s.defaults?.group ?? s.group}
                      list="settings-groups"
                      label={`Group of ${s.name}`}
                      onSave={(v) => change([s.id], { group: v || null }, v ? `${s.name} moved to ${v}` : `${s.name} back in ${s.defaults?.group}`)}
                    />
                    <InlineField
                      className="field-url"
                      value={s.override?.url ?? ''}
                      placeholder={s.defaults?.url ?? 'no link'}
                      label={`Link of ${s.name}`}
                      onSave={(v) => change([s.id], { url: v || null }, v ? `Link set for ${s.name}` : `${s.name} uses its default link`)}
                    />
                    <span className="row-badges">
                      {s.discovered && <span className="auto-badge">auto</span>}
                      {s.override && <span className="auto-badge badge-edited">edited</span>}
                      {s.hidden && <span className="auto-badge badge-hidden">hidden</span>}
                    </span>
                    <span className="row-actions">
                      <button
                        type="button"
                        className="icon-btn"
                        title={s.hidden ? 'Show on the page' : 'Hide from the page'}
                        aria-label={s.hidden ? `Show ${s.name}` : `Hide ${s.name}`}
                        onClick={() => void change([s.id], { hidden: !s.hidden }, s.hidden ? `${s.name} shown` : `${s.name} hidden`)}
                      >
                        <EyeOff size={13} />
                      </button>
                      {s.override && (
                        <button
                          type="button"
                          className="icon-btn"
                          title="Reset to the config's name, group and link"
                          aria-label={`Reset ${s.name}`}
                          onClick={() => void apply(() => api.changeServices([s.id], null, true), `${s.name} reset`)}
                        >
                          <RotateCcw size={13} />
                        </button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )
        })}
        {!byGroup.length && <p className="small muted">Nothing matches.</p>}
      </section>

      <section className="card settings-card">
        <div className="settings-card-head">
          <h3>Groups</h3>
          <span className="small muted">Order on the page, and names. Renaming moves every service in the group.</span>
        </div>
        <ol className="group-order">
          {orderedGroups.map((g, i) => (
            <li key={g}>
              {renaming === g ? (
                <form
                  className="group-rename"
                  onSubmit={(e) => {
                    e.preventDefault()
                    const name = renameTo.trim()
                    if (!name || name === g) {
                      setRenaming(null)
                      return
                    }
                    void apply(() => api.renameGroup(g, name), `${g} renamed to ${name}`).then(() => setRenaming(null))
                  }}
                >
                  <input autoFocus value={renameTo} onChange={(e) => setRenameTo(e.target.value)} aria-label={`New name for ${g}`} />
                  <button type="submit" className="btn btn-low btn-small" disabled={busy}>
                    Rename
                  </button>
                  <button type="button" className="btn btn-ghost btn-small" onClick={() => setRenaming(null)}>
                    Cancel
                  </button>
                </form>
              ) : (
                <>
                  <span className="group-name">{g}</span>
                  <span className="small muted num">{groupCounts[g]}</span>
                  <span className="row-actions">
                    <button type="button" className="icon-btn" disabled={i === 0 || busy} onClick={() => move(i, -1)} aria-label={`Move ${g} up`}>
                      <ArrowUp size={13} />
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      disabled={i === orderedGroups.length - 1 || busy}
                      onClick={() => move(i, 1)}
                      aria-label={`Move ${g} down`}
                    >
                      <ArrowDown size={13} />
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={() => {
                        setRenaming(g)
                        setRenameTo(g)
                      }}
                      aria-label={`Rename ${g}`}
                    >
                      <Pencil size={13} />
                    </button>
                  </span>
                </>
              )}
            </li>
          ))}
        </ol>
      </section>

      <section className="card settings-card settings-files">
        <h3>Still set in files</h3>
        <ul className="small muted">
          <li>Health checks, which containers belong to a service, machines and integrations: config.yaml.</li>
          <li>
            Actions: actions.yaml, read only by the runner. They stay out of the page on purpose: whoever can change an
            action can run commands as root.
          </li>
          <li>New stacks need nothing: they appear on their own, and can be named and grouped here.</li>
        </ul>
      </section>
    </div>
  )
}
