import type { DocType } from './types'

const key = (setId: string) => `emc_shadow_reviewed_docs:${setId}`

export function getReviewedDocs(setId: string): Set<DocType> {
  try { return new Set(JSON.parse(localStorage.getItem(key(setId)) || '[]')) as Set<DocType> }
  catch { return new Set() }
}

export function setDocumentReviewed(setId: string, docType: DocType, reviewed: boolean) {
  const current = getReviewedDocs(setId)
  if (reviewed) current.add(docType); else current.delete(docType)
  localStorage.setItem(key(setId), JSON.stringify([...current]))
  window.dispatchEvent(new CustomEvent('reviewed-docs:change', { detail: { setId } }))
}
