import { useRef, useState } from 'react'
import { useGlobalKeys } from '../shell/keys'
import { Banner } from '../ui/Banner'
import { Duration, Money, Tokens, Clock } from '../ui/figures'
import { ActivityStrip } from '../ui/ActivityStrip'
import { EmptyState } from '../ui/EmptyState'
import { ErrorCard } from '../ui/ErrorCard'
import { FoldRow } from '../ui/FoldRow'
import { foldRows } from '../ui/fold.logic'
import { GroupBody, GroupHeader } from '../ui/GroupHeader'
import { Icon } from '../ui/Icon'
import { KeyCap } from '../ui/KeyCap'
import { MagnitudeBar } from '../ui/MagnitudeBar'
import { ModelMark } from '../ui/ModelMark'
import { Popover } from '../ui/Popover'
import { ProviderMark } from '../ui/ProviderMark'
import { CompactRow, FigureCell, Row, RowName, WhenCell } from '../ui/Row'
import { groupColumns, rowColumns } from '../ui/row.logic'
import { Segmented } from '../ui/Segmented'
import { Skeleton, SkeletonRows } from '../ui/Skeleton'
import { StatStrip } from '../ui/StatStrip'
import { Toast } from '../ui/Toast'
import { Tooltip } from '../ui/Tooltip'

// Development page (`?dev=patterns`, DEV builds only). Every shared pattern in each state, with made-up numbers, so the screenshot
// and contrast gates can see them before any real blade uses them. Not part of the product build.
const TZ = 'Asia/Kolkata'
const T0 = Date.UTC(2026, 9, 12, 4, 0)
const ROWS = Array.from({ length: 9 }, (_, i) => ({
  id: i,
  start: T0 - i * 5 * 3600_000,
  name: ['Fix the flaky parser test', 'Port the blade strip', 'Why is the cache cold', 'Rename the store module', 'Review the pricing table', 'Draft the explorer spec', 'Trim the fixture store', 'Check the contrast report', 'Plan the nightly ingest'][i],
  cost: [2123.3, 549.34, 88.1, 41.5, 12.4, 6.02, 1.1, 0.06, 0.02][i],
  wall: (180 - i * 20) * 60_000,
  tokens: 12_400_000 / (i + 1),
  bins: Array.from({ length: 24 }, (_, b) => (b % 5 === 0 ? 0 : Math.round(1000 * Math.abs(Math.sin((b + i) * 1.3)) * (9 - i)))),
}))
const MAXC = Math.max(...ROWS.map((r) => r.cost))
const MAXB = Math.max(...ROWS.flatMap((r) => r.bins))
const COLS = rowColumns({ context: false })

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h2 className="mb-2 text-body font-semibold text-ink1">{title}</h2>
      {children}
    </section>
  )
}

function ListRow({ r, i, prev, open, cursor }: { r: (typeof ROWS)[number]; i: number; prev: number | null; open?: boolean; cursor?: boolean }) {
  return (
    <Row columns={COLS} open={open} cursor={cursor} data-testid={`row-${i}`}>
      <WhenCell startMs={r.start} prevStartMs={prev} tz={TZ} singleDay={false} clock={<Clock ms={r.start} tz={TZ} lead />} />
      <RowName icon="message-square-text">{r.name}</RowName>
      <span className="flex items-center gap-2">
        <ProviderMark provider="claude_code" />
        <ModelMark model="claude-opus-5-5" compact />
        <span className="inline-flex items-center gap-1 text-ink3"><Icon name="bot" />3</span>
      </span>
      <ActivityStrip bins={r.bins} maxBin={MAXB} wallMs={r.wall} firstMs={r.start} lastMs={r.start + r.wall} tz={TZ} width={200} label="Activity" />
      <FigureCell>
        <MagnitudeBar value={r.cost} max={MAXC} track={96} />
        <Money value={r.cost} />
      </FigureCell>
      <FigureCell><Duration ms={r.wall} /></FigureCell>
      <FigureCell><Duration ms={r.wall / 2} /></FigureCell>
      <FigureCell><Tokens value={r.tokens} ink="ink3" /></FigureCell>
    </Row>
  )
}

export default function Patterns() {
  const [fold, setFold] = useState(false)
  const [open, setOpen] = useState(true)
  const [pop, setPop] = useState(false)
  const [toast, setToast] = useState(false)
  const [seg, setSeg] = useState<'cost' | 'tokens'>('cost')
  const anchor = useRef<HTMLButtonElement>(null)
  // The shell installs the key router in the product; the gallery needs it for Esc on popovers and toasts.
  useGlobalKeys({ bladeKeys: ['gallery'], focusKey: 'gallery', setFocusIndex: () => {}, closeRightmost: () => {}, togglePalette: () => {} })
  const f = foldRows(ROWS, 5, fold, (r) => r.cost)
  return (
    <main className="h-full overflow-auto bg-l1 p-6" data-testid="patterns">
      <Section title="P1 rows: rest, cursor, open, hover (point at it)">
        <div className="w-[1416px]">
          <ListRow r={ROWS[0]} i={0} prev={null} />
          <ListRow r={ROWS[1]} i={1} prev={ROWS[0].start} cursor />
          <ListRow r={ROWS[2]} i={2} prev={ROWS[1].start} open />
          <ListRow r={ROWS[3]} i={3} prev={ROWS[2].start} open cursor />
          <ListRow r={ROWS[4]} i={4} prev={ROWS[3].start} />
        </div>
      </Section>
      <Section title="P2 group header, rows, fold row (SYS-37, SYS-38)">
        <div className="w-[1416px]">
          <GroupHeader
            open={open}
            onToggle={() => setOpen((o) => !o)}
            name="bizdemo"
            count={ROWS.length}
            dot={1}
            columns={groupColumns()}
            magnitude={<ActivityStrip bins={ROWS[0].bins} maxBin={MAXB} wallMs={48 * 3600_000} firstMs={T0} lastMs={T0 + 48 * 3600_000} tz={TZ} width={200} height={32} />}
            totals={
              <>
                <FigureCell><Money value={2822.0} weight={600} /></FigureCell>
                <FigureCell><Duration ms={9 * 3600_000} /></FigureCell>
                <FigureCell><Duration ms={4 * 3600_000} ink="ink3" /></FigureCell>
                <FigureCell><Tokens value={88_000_000} ink="ink3" /></FigureCell>
              </>
            }
          />
          {open ? (
            <GroupBody>
              {f.visible.map((r, i) => <ListRow key={r.id} r={r} i={i + 10} prev={i > 0 ? f.visible[i - 1].start : null} />)}
              {f.label ? <FoldRow label={f.label} open={fold} onToggle={() => setFold((o) => !o)} sum={f.sum === null ? null : <Money value={f.sum} />} /> : null}
            </GroupBody>
          ) : null}
        </div>
      </Section>
      <Section title="Compact rows (400 px) and the rail group header">
        <div className="w-[400px]">
          <GroupHeader open name="bizdemo" count={4} dot={2} compact onToggle={() => {}} />
          <GroupBody>
            {ROWS.slice(0, 3).map((r, i) => (
              <CompactRow
                key={r.id}
                open={i === 1}
                name={r.name}
                figure={<Money value={r.cost} />}
                when={<Clock ms={r.start} tz={TZ} />}
                meta={<Duration ms={r.wall} ink="ink3" />}
                mark={<ActivityStrip bins={r.bins} maxBin={MAXB} wallMs={r.wall} firstMs={r.start} lastMs={r.start + r.wall} tz={TZ} width={88} height={16} />}
              />
            ))}
          </GroupBody>
        </div>
      </Section>
      <Section title="Stat strip, models, figures">
        <StatStrip
          figure={<Money value={2123.3} lowerBound nUnpriced={3} />}
          items={[
            { icon: 'coins', figure: <Tokens value={8_690_000_000} />, word: 'tokens' },
            { icon: 'activity', figure: <Duration ms={5 * 3600_000} />, word: 'active of 9h wall' },
            { icon: 'bot', figure: '12', word: 'agents' },
            { icon: 'book-open', figure: '4', word: 'skill loads' },
          ]}
        >
          <div className="flex items-center gap-4 text-body text-ink3">
            <ModelMark model="claude-opus-5-5" />
            <ModelMark model="claude-sonnet-5-5" />
            <ModelMark model="claude-haiku-5-5" />
            <ModelMark model="claude-fable-5-1" />
            <ModelMark model="gpt-5.4" />
            <ModelMark model="deepseek-v4" />
          </div>
        </StatStrip>
      </Section>
      <Section title="Controls: segmented, key caps, tooltip, popover, toast">
        <div className="flex items-center gap-6">
          <Segmented label="Measure" options={[{ value: 'cost', label: 'Cost' }, { value: 'tokens', label: 'Tokens' }]} value={seg} onChange={setSeg} />
          <KeyCap>⌘K</KeyCap>
          <KeyCap>/</KeyCap>
          <Tooltip content="Excludes 3 calls to unpriced models"><button type="button" className="text-body text-ink2" data-testid="tip-target">Hover for a tooltip</button></Tooltip>
          <button type="button" ref={anchor} onClick={() => setPop((o) => !o)} className="h-8 rounded-control border border-edge px-3 text-body text-ink1 hover:border-struct-line" data-testid="pop-button">Period</button>
          <Popover open={pop} onClose={() => setPop(false)} anchorRef={anchor} label="Period">
            <div className="p-2">
              <h3 className="mb-2 text-body font-semibold text-ink1">Presets</h3>
              {['Today', 'Yesterday', 'Last 7 days'].map((p, i) => (
                <button key={p} type="button" onClick={() => setPop(false)} className={`flex h-9 w-full items-center justify-between rounded-control px-3 text-body hover:bg-l4 ${i === 2 ? 'bg-struct-soft text-struct' : 'text-ink1'}`}>
                  {p}
                  {i === 2 ? <Icon name="check" tone="struct" /> : null}
                </button>
              ))}
            </div>
          </Popover>
          <button type="button" onClick={() => setToast(true)} className="h-8 rounded-control border border-edge px-3 text-body text-ink1" data-testid="toast-button">Show toast</button>
        </div>
        {toast ? <Toast message="Filter removed" onUndo={() => {}} onClose={() => setToast(false)} /> : null}
      </Section>
      <Section title="States: loading, empty, error">
        <div className="grid w-[1000px] grid-cols-3 gap-6">
          <div><SkeletonRows count={3} /><div className="mt-2"><Skeleton width={120} height={30} /></div></div>
          <div className="rounded-card bg-l2"><EmptyState icon="calendar-x" action={{ label: 'Sat Oct 3 · 12 sessions', onClick: () => {} }}>No sessions on Sun Oct 4</EmptyState></div>
          <ErrorCard noun="sessions" message="database is locked" onRetry={() => {}} />
        </div>
      </Section>
      <Section title="Banners (SYS-57, SYS-57a)">
        <div className="w-[1416px] space-y-2">
          <Banner kind="no_store" />
          <Banner kind="rebuild" />
          <Banner kind="ingest" />
        </div>
      </Section>
    </main>
  )
}
