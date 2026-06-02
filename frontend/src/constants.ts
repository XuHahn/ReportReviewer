/** Shared severity/result display constants used across components. */

export const SEV_LABEL: Record<string, string> = {
  error: '错误',
  warning: '警告',
  info: '注意',
}

export type HumanStatus = 'pending' | 'confirmed' | 'false_positive' | 'needs_review' | 'ignored'

export const HUMAN_STATUS_LABEL: Record<HumanStatus, string> = {
  pending: '待复核',
  confirmed: '已修正',
  false_positive: '非错误',
  needs_review: '需复核',
  ignored: '已忽略',
}

export const HUMAN_STATUS_OPTIONS: { value: HumanStatus; label: string }[] = [
  { value: 'pending', label: '待复核' },
  { value: 'confirmed', label: '已修正' },
  { value: 'false_positive', label: '非错误' },
  { value: 'needs_review', label: '需复核' },
  { value: 'ignored', label: '已忽略' },
]
