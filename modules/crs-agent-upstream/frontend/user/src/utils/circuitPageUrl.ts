export interface CircuitPageUrlOptions {
  fileUrl: string
  pageNumber?: number
  points?: string
}

function normalizedPage(pageNumber?: number): number | null {
  const page = Math.floor(Number(pageNumber || 0))
  return page > 0 ? page : null
}

function appendOrReplaceTextParam(value: string, separator: string, key: string, rawParamValue: string): string {
  const encodedValue = key === 'points' ? rawParamValue : encodeURIComponent(rawParamValue)
  const param = `${key}=${encodedValue}`
  const pattern = new RegExp(`(^|[?&])${key}=[^&#]*`)
  if (pattern.test(value)) {
    return value.replace(pattern, (_match, prefix: string) => `${prefix}${param}`)
  }
  return `${value}${separator}${param}`
}

function appendParamsToQueryLikeValue(value: string, params: Array<[string, string]>): string {
  let nextValue = value
  for (const [key, rawParamValue] of params) {
    const separator = nextValue && !nextValue.endsWith('&') && !nextValue.endsWith('?') ? '&' : ''
    nextValue = appendOrReplaceTextParam(nextValue, separator, key, rawParamValue)
  }
  return nextValue
}

function appendParamsToUrl(value: string, params: Array<[string, string]>): string {
  let nextValue = value
  for (const [key, rawParamValue] of params) {
    const separator = nextValue.includes('?')
      ? (nextValue.endsWith('?') || nextValue.endsWith('&') ? '' : '&')
      : '?'
    nextValue = appendOrReplaceTextParam(nextValue, separator, key, rawParamValue)
  }
  return nextValue
}

function appendParamsToHashUrl(rawUrl: string, params: Array<[string, string]>): string {
  const hashIndex = rawUrl.indexOf('#')
  const baseUrl = rawUrl.slice(0, hashIndex)
  const hash = rawUrl.slice(hashIndex + 1)

  if (!hash) {
    return `${baseUrl}#${appendParamsToQueryLikeValue('', params)}`
  }

  if (hash.includes('?')) {
    const queryIndex = hash.indexOf('?')
    const hashPrefix = hash.slice(0, queryIndex + 1)
    const hashQuery = hash.slice(queryIndex + 1)
    return `${baseUrl}#${hashPrefix}${appendParamsToQueryLikeValue(hashQuery, params)}`
  }

  if (hash.includes('=') || hash.includes('&')) {
    return `${baseUrl}#${appendParamsToQueryLikeValue(hash, params)}`
  }

  return `${baseUrl}#${hash}?${appendParamsToQueryLikeValue('', params)}`
}

export function buildCircuitPageUrl({
  fileUrl,
  pageNumber,
  points,
}: CircuitPageUrlOptions): string {
  const rawUrl = String(fileUrl || '').trim()
  if (!rawUrl) {
    return ''
  }

  const page = normalizedPage(pageNumber)
  const normalizedPoints = String(points || '').trim()
  const params: Array<[string, string]> = []
  if (page !== null) {
    params.push(['page', String(page)])
  }
  if (normalizedPoints) {
    params.push(['points', normalizedPoints])
  }
  if (params.length === 0) {
    return rawUrl
  }

  if (rawUrl.includes('#')) {
    return appendParamsToHashUrl(rawUrl, params)
  }

  return appendParamsToUrl(rawUrl, params)
}
