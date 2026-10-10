import { Clapperboard, Library as LibraryIcon, Tv } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type Library, type LibraryItem, type MediaLibrary } from '../api'
import { ago, bytes } from '../format'
import { useApp, useClock } from '../state'
import { day, signed, SizeChart } from './Backups'
import { Empty, Facts, Num, RowButton, SourceNote } from './ui'

function plural(n: number | null | undefined, word: string): string | null {
  if (n == null) return null
  return `${n.toLocaleString()} ${n === 1 ? word : `${word}s`}`
}

/** What a library holds, in its own terms: movies, or shows and episodes. */
export function libraryCounts(lib: MediaLibrary): string {
  const parts =
    lib.kind === 'movies'
      ? [plural(lib.movies, 'movie')]
      : lib.kind === 'tvshows'
        ? [plural(lib.series, 'show'), plural(lib.episodes, 'episode')]
        : lib.kind === 'boxsets'
          ? [plural(lib.collections, 'collection')]
          : lib.kind === 'music'
            ? [plural(lib.albums, 'album'), plural(lib.songs, 'song')]
            : [plural(lib.items, 'item')]
  return parts.filter(Boolean).join(' · ')
}

export function LibraryPoster({ item, large = false }: { item: LibraryItem; large?: boolean }) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    return (
      <span className={`poster poster-empty${large ? ' poster-large' : ''}`} aria-hidden="true">
        {item.kind === 'series' ? <Tv size={16} /> : <Clapperboard size={16} />}
      </span>
    )
  }
  return (
    <img
      className={`poster${large ? ' poster-large' : ''}`}
      src={`/api/media/library/poster/${item.id}`}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  )
}

function LibraryRows({ library }: { library: Library }) {
  const sized = library.libraries.filter((l) => l.kind !== 'boxsets')
  const max = Math.max(1, ...sized.map((l) => l.bytes ?? 0))
  return (
    <ul className="library-rows">
      {sized.map((l) => (
        <li key={l.id}>
          <span className="library-name">{l.name}</span>
          <span className="small muted num">{libraryCounts(l)}</span>
          <span className="small num library-size">{l.bytes != null ? bytes(l.bytes) : ''}</span>
          {l.bytes != null && (
            <span className="queue-bar library-bar">
              <span className="queue-fill library-fill" style={{ width: `${Math.round((l.bytes / max) * 100)}%` }} />
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

/** The library card on the Holonet deck. */
export function LibraryCard({ library }: { library: Library }) {
  const { open } = useApp()
  const t = library.totals
  return (
    <article className="edge-card card media-card library-card">
      <div className="edge-head">
        <LibraryIcon size={16} />
        <h3>Library</h3>
        {t.bytes != null && <span className="small muted num">{bytes(t.bytes)}</span>}
      </div>
      {library.error && <p className="small warn-text">Library unavailable: {library.error}</p>}
      {!library.ok && !library.error && <Empty>Counting the library…</Empty>}
      {library.ok && (
        <>
          <RowButton className="library-stats" onClick={() => open('library')}>
            <span className="tile-stat">
              <Num value={t.movies ?? 0} format={(v) => Math.round(v).toLocaleString()} className="tile-big" />
              <span className="small muted">movies</span>
            </span>
            <span className="tile-stat">
              <Num value={t.series ?? 0} format={(v) => Math.round(v).toLocaleString()} className="tile-big" />
              <span className="small muted">shows</span>
            </span>
            <span className="tile-stat">
              <Num value={t.episodes ?? 0} format={(v) => Math.round(v).toLocaleString()} className="tile-big" />
              <span className="small muted">episodes</span>
            </span>
          </RowButton>
          <LibraryRows library={library} />
          {library.latest.length > 0 && (
            <>
              <span className="small muted">Recently added</span>
              <div className="poster-strip">
                {library.latest.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="poster-btn"
                    onClick={() => open('library')}
                    title={`${item.title}${item.detail ? ` · ${item.detail}` : ''}`}
                  >
                    <LibraryPoster item={item} />
                  </button>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </article>
  )
}

export function LibraryDrawer() {
  const { snapshot } = useApp()
  const now = useClock()
  const library = snapshot?.library
  const [days, setDays] = useState<{ date: string; bytes: number | null }[] | null>(null)
  useEffect(() => {
    let live = true
    api
      .libraryHistory()
      .then((h) => live && setDays(h.days))
      .catch(() => live && setDays([]))
    return () => {
      live = false
    }
  }, [])
  if (!library) return <Empty>Jellyfin is not configured.</Empty>
  const t = library.totals
  const sizes = (days ?? []).filter((d) => d.bytes != null).map((d) => ({ date: d.date, bytes: d.bytes as number }))
  const collections = library.libraries.find((l) => l.kind === 'boxsets')
  return (
    <div className="library-drawer">
      <div className="drawer-kicker">
        <LibraryIcon size={14} /> Jellyfin
      </div>
      <h3 className="drawer-title">Media library</h3>
      {library.error && <p className="small warn-text">{library.error}</p>}
      <div className="recap-stats">
        <div className="recap-stat">
          <Num value={t.bytes ?? null} format={(v) => bytes(v)} className="recap-big" />
          <span className="small muted">on disk</span>
        </div>
        <div className="recap-stat">
          <Num value={t.movies ?? 0} format={(v) => Math.round(v).toLocaleString()} className="recap-big" />
          <span className="small muted">movies</span>
        </div>
        <div className="recap-stat">
          <Num value={t.series ?? 0} format={(v) => Math.round(v).toLocaleString()} className="recap-big" />
          <span className="small muted">shows</span>
        </div>
        <div className="recap-stat">
          <Num value={t.episodes ?? 0} format={(v) => Math.round(v).toLocaleString()} className="recap-big" />
          <span className="small muted">episodes</span>
        </div>
      </div>

      <h4 className="drawer-sub">Libraries</h4>
      <LibraryRows library={library} />

      <h4 className="drawer-sub">Growth</h4>
      {sizes.length > 1 ? (
        <SizeChart series={sizes} />
      ) : (
        <p className="small muted">
          Executor records the library size once a day; the chart fills in from{' '}
          {library.growth ? day(library.growth.tracked_since) : 'today'}.
        </p>
      )}
      <Facts
        items={[
          ['Last 7 days', library.growth?.d7 != null ? signed(library.growth.d7) : null],
          ['Last 30 days', library.growth?.d30 != null ? signed(library.growth.d30) : null],
          ['Files', (() => {
            const files = library.libraries.reduce((sum, l) => sum + (l.files ?? 0), 0)
            return files ? files.toLocaleString() : null
          })()],
          ['Collections', collections ? libraryCounts(collections) : null],
        ]}
      />

      <h4 className="drawer-sub">Recently added</h4>
      {library.latest.length ? (
        <ul className="library-latest">
          {library.latest.map((item) => (
            <li key={item.id}>
              <LibraryPoster item={item} />
              <span className="library-latest-text">
                <span>
                  {item.title}
                  {item.year ? <span className="muted"> ({item.year})</span> : null}
                </span>
                <span className="small muted">
                  {[item.kind === 'series' ? item.detail : 'Movie', item.added ? ago(item.added, now) : null]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>Nothing added yet.</Empty>
      )}
      <SourceNote source="Jellyfin (counts and recent additions) and the runner (folder sizes)" at={library.checked_at} />
    </div>
  )
}
