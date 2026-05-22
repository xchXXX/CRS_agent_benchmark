import { buildCircuitPageUrl } from './circuitPageUrl'
import { getSafeVisitUrl } from './urlUtils'

export interface DocumentViewUrlOptions {
  picFolderUrl: string
  urlType?: string
  initialPage?: number
  points?: string
}

export function buildDocumentViewUrl({
  picFolderUrl,
  urlType,
  initialPage,
  points,
}: DocumentViewUrlOptions): string {
  const rawUrl = String(picFolderUrl || '').trim()
  if (!rawUrl) {
    return ''
  }

  const baseViewUrl = urlType === 'pic_folder' || !urlType
    ? getSafeVisitUrl(rawUrl)
    : rawUrl

  if (!baseViewUrl) {
    return ''
  }

  if ((!initialPage || initialPage < 1) && !String(points || '').trim()) {
    return baseViewUrl
  }

  return buildCircuitPageUrl({
    fileUrl: baseViewUrl,
    pageNumber: initialPage,
    points,
  })
}
