import type { SVGProps } from 'react'

export type IconName =
  | 'arrow-left' | 'check' | 'chevron-down' | 'chevron-right' | 'close'
  | 'download' | 'expand' | 'file' | 'filter' | 'history' | 'play' | 'plus'
  | 'refresh' | 'search' | 'settings' | 'warning'

interface Props extends SVGProps<SVGSVGElement> {
  name: IconName
  size?: number
}

const paths: Record<IconName, JSX.Element> = {
  'arrow-left': <><path d="M19 12H5"/><path d="m11 18-6-6 6-6"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  'chevron-down': <path d="m6 9 6 6 6-6"/>,
  'chevron-right': <path d="m9 6 6 6-6 6"/>,
  close: <><path d="m6 6 12 12"/><path d="M18 6 6 18"/></>,
  download: <><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></>,
  expand: <><path d="M8 3H3v5M16 3h5v5M21 16v5h-5M3 16v5h5"/><path d="m3 8 6-6M21 8l-6-6M21 16l-6 6M3 16l6 6"/></>,
  file: <><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/></>,
  filter: <path d="M4 6h16M7 12h10M10 18h4"/>,
  history: <><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/></>,
  play: <path d="m8 5 11 7-11 7z"/>,
  plus: <><path d="M12 5v14"/><path d="M5 12h14"/></>,
  refresh: <><path d="M20 7v5h-5"/><path d="M4 17v-5h5"/><path d="M6.1 8a7 7 0 0 1 11.4-2L20 9M4 15l2.5 3a7 7 0 0 0 11.4-2"/></>,
  search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
  warning: <><path d="M12 3 2.8 20h18.4z"/><path d="M12 9v4"/><path d="M12 17h.01"/></>,
}

export default function Icon({ name, size = 18, ...props }: Props) {
  return <svg
    viewBox="0 0 24 24" width={size} height={size} fill="none"
    stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
    aria-hidden="true" focusable="false" {...props}
  >{paths[name]}</svg>
}
