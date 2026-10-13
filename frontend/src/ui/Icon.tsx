import {
  Activity, ArrowDownNarrowWide, ArrowLeftRight, ArrowUpNarrowWide, ArrowUpRight, BookOpen, Bot, Brain, Calendar, CalendarX,
  Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, CircleAlert, CircleDashed, CircleDollarSign, Clock, Coins, Command,
  Copy, Cpu, Crosshair, Database, Ear, FilePen, FilePlus, FileText, GitBranch, GitFork, Globe, Hourglass, Info, Keyboard, Layers,
  ListCollapse, MessageSquare, MessageSquareText, MessagesSquare, Plug, Puzzle, Repeat, RotateCcw, Rows3, Search, SearchCode,
  SearchX, Send, SlidersHorizontal, SquareSlash, Terminal, Timer, TimerOff, UserRound, X, type LucideIcon,
} from 'lucide-react'
import type { IconName } from './glyphs'

// Every SYS-28 icon, by Lucide name. The Record type makes a missing entry a compile error.
const ICONS: Record<IconName, LucideIcon> = {
  'rows-3': Rows3, 'message-square-text': MessageSquareText, 'square-slash': SquareSlash, plug: Plug, 'messages-square': MessagesSquare,
  'rotate-ccw': RotateCcw, hourglass: Hourglass, 'timer-off': TimerOff, ear: Ear, 'list-collapse': ListCollapse,
  'circle-dashed': CircleDashed, crosshair: Crosshair, keyboard: Keyboard, terminal: Terminal, 'message-square': MessageSquare,
  brain: Brain, 'arrow-left-right': ArrowLeftRight, bot: Bot, 'user-round': UserRound, send: Send, 'git-fork': GitFork,
  'file-text': FileText, 'file-pen': FilePen, 'file-plus': FilePlus, 'search-code': SearchCode, globe: Globe, 'book-open': BookOpen,
  puzzle: Puzzle, 'circle-alert': CircleAlert, info: Info, cpu: Cpu, 'git-branch': GitBranch, repeat: Repeat,
  'circle-dollar-sign': CircleDollarSign, coins: Coins, timer: Timer, activity: Activity, database: Database, calendar: Calendar,
  clock: Clock, 'calendar-x': CalendarX, search: Search, 'search-x': SearchX, 'sliders-horizontal': SlidersHorizontal, layers: Layers,
  x: X, command: Command, 'chevron-right': ChevronRight, 'chevron-up': ChevronUp, 'chevron-down': ChevronDown,
  'arrow-up-right': ArrowUpRight, 'chevron-left': ChevronLeft, copy: Copy, check: Check,
  'arrow-down-narrow-wide': ArrowDownNarrowWide, 'arrow-up-narrow-wide': ArrowUpNarrowWide,
}

/** SYS-26 tones: quiet = ink2 beside ink1 text; muted = ink3; struct and error as stated there. */
const TONE = { quiet: 'text-ink2', muted: 'text-ink3', struct: 'text-struct', error: 'text-error', inherit: '' } as const
export type IconTone = keyof typeof TONE

interface IconProps {
  name: IconName
  /** SYS-25: 16 inline, 20 beside a 20 px title, 24 in empty states. */
  size?: 16 | 20 | 24
  tone?: IconTone
  /** Set when the icon is the only label (SYS-27); otherwise it is decoration for the text beside it. */
  label?: string
  className?: string
}

export function Icon({ name, size = 16, tone = 'muted', label, className = '' }: IconProps) {
  const Cmp = ICONS[name]
  return (
    <Cmp
      size={size}
      strokeWidth={1.5}
      absoluteStrokeWidth
      aria-label={label}
      aria-hidden={label ? undefined : true}
      role={label ? 'img' : undefined}
      className={`shrink-0 ${TONE[tone]} ${className}`}
      style={{ verticalAlign: size === 16 ? -3 : undefined }}
    />
  )
}

export { ICONS }
