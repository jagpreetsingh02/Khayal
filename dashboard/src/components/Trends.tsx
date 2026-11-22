import { useEffect, useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { Limits, Reading } from '../api'
import { usePoll } from '../hooks'
import { istDay, istTime, istWeekday, istWhen } from '../time'
import { Card, ErrorNote, Skeleton } from './ui'

type Range = '7d' | '14d' | '30d'
const DAYS: Record<Range, number> = { '7d': 7, '14d': 14, '30d': 30 }
const DAY_MS = 86_400_000
const IST_OFFSET_MS = 5.5 * 3_600_000

interface Colors {
  s1: string
  s2: string
  warn: string
  bad: string
  grid: string
  axis: string
  surface: string
}

function readColors(): Colors {
  const css = getComputedStyle(document.documentElement)
  const v = (name: string) => css.getPropertyValue(name).trim()
  return {
    s1: v('--series-1'),
    s2: v('--series-2'),
    warn: v('--status-warning'),
    bad: v('--status-critical'),
    grid: v('--grid'),
    axis: v('--text-muted'),
    surface: v('--surface'),
  }
}

/** Chart colors from the CSS tokens, re-read when the OS switches light/dark. */
function useColors(): Colors {
  const [colors, setColors] = useState(readColors)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const update = () => setColors(readColors())
    mq.addEventListener('change', update)
    return () => mq.removeEventListener('change', update)
  }, [])
  return colors
}

const steps = (from: number, to: number, by: number) =>
  Array.from({ length: Math.floor((to - from) / by) + 1 }, (_, i) => from + i * by)

/** Midnight ticks (Asia/Kolkata) across the range. */
function dayTicks(from: number, to: number, every: number): number[] {
  const first = Math.ceil((from + IST_OFFSET_MS) / DAY_MS) * DAY_MS - IST_OFFSET_MS
  const ticks: number[] = []
  for (let t = first; t <= to; t += DAY_MS * every) ticks.push(t)
  return ticks
}

interface TipPayload {
  payload: Record<string, number | string | null>
}

function BpTip({ active, payload }: { active?: boolean; payload?: TipPayload[] }) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="chart-tip">
      <strong>{istWhen(new Date(p.t as number).toISOString())}</strong>
      <span>
        <i className="key key-1" /> Systolic {p.sys}
      </span>
      <span>
        <i className="key key-2" /> Diastolic {p.dia}
      </span>
      {p.pulse ? <span className="muted">Pulse {p.pulse}</span> : null}
    </div>
  )
}

function GlucoseTip({ active, payload }: { active?: boolean; payload?: TipPayload[] }) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="chart-tip">
      <strong>{istWhen(new Date(p.t as number).toISOString())}</strong>
      <span>
        <i className="key key-1" /> {p.value} mg/dL
      </span>
      {p.context ? <span className="muted">{String(p.context).replace('_', ' ')}</span> : null}
    </div>
  )
}

/** Direct label at the last point of a line (identity never by color alone). */
function endLabel(text: string, color: string, count: number) {
  return function EndLabel(props: { x?: number | string; y?: number | string; index?: number }) {
    if (props.index !== count - 1 || props.x == null || props.y == null) return null
    return (
      <text x={Number(props.x) + 8} y={Number(props.y) + 4} fill={color} fontSize={12} fontWeight={600}>
        {text}
      </text>
    )
  }
}

function BpChart({ rows, limits, from, to, c }: {
  rows: Reading[]
  limits: Limits
  from: number
  to: number
  c: Colors
}) {
  const data = rows.map((r) => ({
    t: new Date(r.at).getTime(),
    sys: r.systolic,
    dia: r.diastolic,
    pulse: r.pulse,
  }))
  const top = Math.ceil(Math.max(200, ...data.map((d) => (d.sys ?? 0) + 10)) / 20) * 20
  const ticks = dayTicks(from, to, to - from > 15 * DAY_MS ? 5 : 2)
  const axis = { fill: c.axis, fontSize: 12 }
  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={data} margin={{ top: 12, right: 64, bottom: 4, left: -16 }}>
        <CartesianGrid stroke={c.grid} vertical={false} />
        <ReferenceArea
          y1={limits.bp_limit_systolic}
          y2={limits.bp_severe_systolic}
          fill={c.warn}
          fillOpacity={0.14}
          ifOverflow="hidden"
        />
        <ReferenceArea
          y1={limits.bp_severe_systolic}
          y2={top}
          fill={c.bad}
          fillOpacity={0.12}
          ifOverflow="hidden"
        />
        <ReferenceLine
          y={limits.bp_limit_systolic}
          stroke={c.warn}
          strokeDasharray="4 4"
        />
        <ReferenceLine
          y={limits.bp_limit_diastolic}
          stroke={c.warn}
          strokeDasharray="4 4"
        />
        <ReferenceLine
          y={limits.bp_severe_systolic}
          stroke={c.bad}
          strokeDasharray="4 4"
        />
        <XAxis
          dataKey="t"
          type="number"
          scale="time"
          domain={[from, to]}
          ticks={ticks}
          tickFormatter={istDay}
          tick={axis}
          stroke={c.grid}
        />
        <YAxis domain={[60, top]} ticks={steps(60, top, 20)} tick={axis} stroke={c.grid} width={56} />
        <Tooltip content={<BpTip />} cursor={{ stroke: c.axis, strokeDasharray: '3 3' }} />
        <Line
          type="linear"
          dataKey="sys"
          name="Systolic"
          stroke={c.s1}
          strokeWidth={2}
          dot={{ r: 3, fill: c.s1, stroke: c.surface, strokeWidth: 2 }}
          activeDot={{ r: 5, stroke: c.surface, strokeWidth: 2 }}
          label={endLabel('Systolic', c.s1, data.length)}
          isAnimationActive={false}
        />
        <Line
          type="linear"
          dataKey="dia"
          name="Diastolic"
          stroke={c.s2}
          strokeWidth={2}
          dot={{ r: 3, fill: c.s2, stroke: c.surface, strokeWidth: 2 }}
          activeDot={{ r: 5, stroke: c.surface, strokeWidth: 2 }}
          label={endLabel('Diastolic', c.s2, data.length)}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

function GlucoseChart({ rows, limits, from, to, c }: {
  rows: Reading[]
  limits: Limits
  from: number
  to: number
  c: Colors
}) {
  const data = rows.map((r) => ({
    t: new Date(r.at).getTime(),
    value: r.glucose_mgdl,
    context: r.context,
  }))
  const top = Math.ceil(Math.max(300, ...data.map((d) => (d.value ?? 0) + 20)) / 50) * 50
  const ticks = dayTicks(from, to, to - from > 15 * DAY_MS ? 5 : 2)
  const axis = { fill: c.axis, fontSize: 12 }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 12, right: 24, bottom: 4, left: -16 }}>
        <CartesianGrid stroke={c.grid} vertical={false} />
        <ReferenceArea y1={40} y2={limits.glucose_red_below} fill={c.bad} fillOpacity={0.12} ifOverflow="hidden" />
        <ReferenceArea
          y1={limits.glucose_red_below}
          y2={limits.glucose_amber_below}
          fill={c.warn}
          fillOpacity={0.14}
          ifOverflow="hidden"
        />
        <ReferenceArea y1={limits.glucose_amber_above} y2={top} fill={c.warn} fillOpacity={0.14} ifOverflow="hidden" />
        <ReferenceLine
          y={limits.glucose_amber_above}
          stroke={c.warn}
          strokeDasharray="4 4"
        />
        <ReferenceLine
          y={limits.glucose_amber_below}
          stroke={c.warn}
          strokeDasharray="4 4"
        />
        <XAxis
          dataKey="t"
          type="number"
          scale="time"
          domain={[from, to]}
          ticks={ticks}
          tickFormatter={istDay}
          tick={axis}
          stroke={c.grid}
        />
        <YAxis domain={[40, top]} ticks={steps(50, top, 50)} tick={axis} stroke={c.grid} width={56} />
        <Tooltip content={<GlucoseTip />} cursor={{ stroke: c.axis, strokeDasharray: '3 3' }} />
        <Line
          type="linear"
          dataKey="value"
          name="Glucose"
          stroke={c.s1}
          strokeWidth={2}
          dot={{ r: 4, fill: c.s1, stroke: c.surface, strokeWidth: 2 }}
          activeDot={{ r: 6, stroke: c.surface, strokeWidth: 2 }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

function ReadingTable({ rows }: { rows: Reading[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>When (IST)</th>
            <th>Reading</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>
          {[...rows].reverse().map((r) => (
            <tr key={r.id}>
              <td>
                {istWeekday(r.at)}, {istTime(r.at)}
              </td>
              <td>
                {r.type === 'bp'
                  ? `${r.systolic}/${r.diastolic} mmHg`
                  : `${r.glucose_mgdl} mg/dL`}
              </td>
              <td className="muted">
                {r.type === 'bp' ? (r.pulse ? `Pulse ${r.pulse}` : '') : (r.context ?? '').replace('_', ' ')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function TrendsPage({ parentId, parent }: { parentId: string; parent: string }) {
  const [range, setRange] = useState<Range>('14d')
  const [table, setTable] = useState(false)
  const { data, error } = usePoll<{ readings: Reading[]; limits: Limits; now: string }>(
    `/parents/${parentId}/readings?range=${range}`,
  )
  const c = useColors()
  const to = data ? new Date(data.now).getTime() : 0
  const from = to - DAYS[range] * DAY_MS
  const bp = data?.readings.filter((r) => r.type === 'bp') ?? []
  const glucose = data?.readings.filter((r) => r.type === 'glucose') ?? []

  return (
    <div className="page">
      <div className="toolbar">
        <div className="segmented" role="group" aria-label="Time range">
          {(Object.keys(DAYS) as Range[]).map((r) => (
            <button key={r} aria-pressed={range === r} onClick={() => setRange(r)}>
              {DAYS[r]} days
            </button>
          ))}
        </div>
        <button className="btn btn-small btn-ghost" onClick={() => setTable(!table)}>
          {table ? 'Show charts' : 'Show as table'}
        </button>
      </div>
      <ErrorNote error={error} />
      {!data ? (
        <Skeleton lines={8} />
      ) : (
        <>
          <Card
            title="Blood pressure"
            action={
              <div className="legend" aria-label="Legend">
                <span>
                  <i className="key key-1" /> Systolic
                </span>
                <span>
                  <i className="key key-2" /> Diastolic
                </span>
              </div>
            }
          >
            {bp.length === 0 ? (
              <p className="empty">No confirmed BP readings in this range.</p>
            ) : table ? (
              <ReadingTable rows={bp} />
            ) : (
              <BpChart rows={bp} limits={data.limits} from={from} to={to} c={c} />
            )}
            <p className="band-key">
              <span><i className="swatch swatch-warn" /> Systolic above the limit ({data.limits.bp_limit_systolic})</span>
              <span><i className="swatch swatch-bad" /> Severe ({data.limits.bp_severe_systolic}/{data.limits.bp_severe_diastolic}): family alerted only after a repeat reading</span>
              <span><i className="dash" /> Diastolic limit ({data.limits.bp_limit_diastolic})</span>
            </p>
          </Card>
          <Card title="Glucose" action={<span className="muted small">mg/dL</span>}>
            {glucose.length === 0 ? (
              <p className="empty">No confirmed glucose readings in this range.</p>
            ) : table ? (
              <ReadingTable rows={glucose} />
            ) : (
              <GlucoseChart rows={glucose} limits={data.limits} from={from} to={to} c={c} />
            )}
            <p className="band-key">
              <span><i className="swatch swatch-warn" /> Amber: below {data.limits.glucose_amber_below} or above {data.limits.glucose_amber_above}</span>
              <span><i className="swatch swatch-bad" /> Red: below {data.limits.glucose_red_below}</span>
            </p>
          </Card>
          <p className="footnote">
            Shaded bands are the limits set by {parent}'s doctor. Only readings {parent} confirmed
            are shown. Times are in India time (IST).
          </p>
        </>
      )}
    </div>
  )
}
