import { ChevronUp, Map as MapIcon, X } from 'lucide-react'
import { lazy, type ReactNode, Suspense, useEffect, useRef, useState } from 'react'
import { collectAlerts } from '../alerts'
import type { DrawerRef } from '../route'
import { ShowMapContext, useApp } from '../state'
import { ActionDrawer, RunDrawer } from './ActionKit'
import { BackupJobDrawer, FileBackupDrawer, StorageDrawer } from './Backups'
import { BandwidthDrawer, CertificateDrawer } from './Edge'
import { DnsDrawer } from './Dns'
import { FeedDrawer } from './Feed'
import { LibraryDrawer } from './Library'
import { DiskDrawer, NasDrawer, PoolDrawer } from './Storage'
import { SiteDrawer, ThreatsDrawer } from './Traffic'
import { DownloadDrawer, PlaceDrawer, RequestDrawer, RouteDrawer, StreamDrawer, UserDrawer } from './MediaDrawers'
import { PhotosDrawer } from './Photos'
import { RecapDrawer } from './Recap'
import { ContainerDrawer, ServiceDrawer } from './ServiceDrawer'
import { Empty, RowButton } from './ui'

// Charts are the heaviest part of a drawer; load them only when a machine opens.
const MachineDrawer = lazy(() => import('./MachineDrawer').then((m) => ({ default: m.MachineDrawer })))

function AlertsDrawer() {
  const { snapshot, openRef, showDeck } = useApp()
  const alerts = collectAlerts(snapshot)
  return (
    <div>
      <div className="drawer-kicker">Alerts</div>
      <h3 className="drawer-title">{alerts.length ? `${alerts.length} need attention` : 'All systems nominal'}</h3>
      {!alerts.length && <Empty>Nothing needs attention.</Empty>}
      <ul className="alert-list">
        {alerts.map((a) => (
          <li key={a.key}>
            <RowButton
              className={`alert-item alert-${a.level}`}
              onClick={() => (a.ref ? openRef(a.ref, a.deck) : showDeck(a.deck))}
            >
              <span className={`dot dot-${a.level === 'bad' ? 'down' : 'degraded'}`} />
              <span className="alert-text">
                <span>{a.title}</span>
                {a.detail && <span className="small muted">{a.detail}</span>}
              </span>
            </RowButton>
          </li>
        ))}
      </ul>
    </div>
  )
}

function content(d: DrawerRef): ReactNode {
  switch (d.kind) {
    case 'machine':
      return <MachineDrawer id={d.id} />
    case 'service':
      return <ServiceDrawer id={d.id} />
    case 'container':
      return <ContainerDrawer name={d.id} />
    case 'certificate':
      return <CertificateDrawer host={d.id} />
    case 'bandwidth':
      return <BandwidthDrawer />
    case 'stream':
      return <StreamDrawer id={d.id} />
    case 'route':
      return <RouteDrawer />
    case 'place':
      return <PlaceDrawer id={d.id} />
    case 'user':
      return <UserDrawer id={d.id} />
    case 'request':
      return <RequestDrawer id={d.id} />
    case 'download':
      return <DownloadDrawer id={d.id} />
    case 'backup':
      return <BackupJobDrawer id={d.id} />
    case 'files':
      return <FileBackupDrawer name={d.id} />
    case 'storage':
      return <StorageDrawer name={d.id} />
    case 'photos':
      return <PhotosDrawer />
    case 'library':
      return <LibraryDrawer />
    case 'pool':
      return <PoolDrawer name={d.id} />
    case 'disk':
      return <DiskDrawer name={d.id} />
    case 'nas':
      return <NasDrawer />
    case 'dns':
      return <DnsDrawer />
    case 'site':
      return <SiteDrawer site={d.id} />
    case 'threats':
      return <ThreatsDrawer />
    case 'action':
      return <ActionDrawer id={d.id} />
    case 'run':
      return <RunDrawer id={d.id} />
    case 'feed':
      return <FeedDrawer />
    case 'recap':
      return <RecapDrawer />
    case 'alerts':
      return <AlertsDrawer />
    default:
      return <Empty>Nothing to show here.</Empty>
  }
}

// On Holonet these drawers explain what the map is showing (a viewer's path, a
// stream, a place, the route), so the map stays lit and usable beside them.
const MAP_KINDS = new Set(['user', 'stream', 'place', 'route'])
const PEEK_LABELS: Record<string, string> = { user: 'Viewer and path', stream: 'Stream', place: 'Place', route: 'Stream route' }

/** One side drawer for every detail view; full screen on a phone. */
export function DrawerHost() {
  const { route, close } = useApp()
  const panel = useRef<HTMLElement>(null)
  const d = route.drawer
  const docked = !!d && route.deck === 'holonet' && MAP_KINDS.has(d.kind)
  // On a phone a docked drawer can shrink to a bar so the map behind it can be
  // used (a viewer's path replayed). It opens in full again for each new drawer,
  // because the host is mounted afresh per drawer.
  const [peek, setPeek] = useState(false)

  useEffect(() => {
    if (!d) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !document.fullscreenElement) close()
    }
    document.addEventListener('keydown', onKey)
    panel.current?.focus({ preventScroll: true })
    return () => document.removeEventListener('keydown', onKey)
  }, [d, close])

  useEffect(() => {
    document.documentElement.classList.toggle('drawer-open', !!d && d.kind !== 'focus' && !peek)
    document.documentElement.classList.toggle('drawer-docked', docked)
  }, [d, docked, peek])

  const showMap = () => {
    setPeek(true)
    // The map heads the deck, and the page may be scrolled down to the card that opened this.
    document.querySelector('.explorer')?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }

  // "focus" only tells the settings page which service to show; it has no drawer.
  if (!d || d.kind === 'focus') return null
  if (peek) {
    return (
      <div className="drawer-peek" role="region" aria-label="Details, folded away">
        <button type="button" className="drawer-peek-open" onClick={() => setPeek(false)}>
          <ChevronUp size={16} />
          <span>{PEEK_LABELS[d.kind] ?? 'Details'}</span>
          <span className="small muted">show details</span>
        </button>
        <button type="button" className="icon-btn" onClick={close} aria-label="Close details">
          <X size={16} />
        </button>
      </div>
    )
  }
  return (
    <ShowMapContext.Provider value={docked ? showMap : null}>
      {/* The page behind dims, and a click on it closes the drawer. A docked drawer
          leaves the page alone, so the map's own controls (replay) keep working. */}
      {!docked && <div className="drawer-backdrop" onClick={close} aria-hidden="true" />}
      <aside className="drawer" ref={panel} tabIndex={-1} role="dialog" aria-label="Details">
        {docked && (
          <button type="button" className="btn btn-ghost btn-small drawer-map" onClick={showMap}>
            <MapIcon size={14} /> Map
          </button>
        )}
        <button type="button" className="icon-btn drawer-close" onClick={close} aria-label="Close details">
          <X size={18} />
        </button>
        <div className="drawer-body" key={`${d.kind}:${d.id}`}>
          <Suspense fallback={<p className="muted small">Loading…</p>}>{content(d)}</Suspense>
        </div>
      </aside>
    </ShowMapContext.Provider>
  )
}
