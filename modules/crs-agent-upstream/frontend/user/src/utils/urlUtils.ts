/**
 * URL 转换工具函数
 * 将 pic_folder_url 转换为安全可访问的最终 URL
 */

/**
 * 将文件名中的特殊字符替换为下划线
 */
function getSafeFileName(fileName: string): string {
  const pattern = /[+\s?？！@#￥%…&*（）=·~!$^()/<>,;':"[\]{}]/g
  return fileName.replace(pattern, '_')
}

/**
 * 接收 pic_folder_url，返回安全可访问的最终 URL
 *
 * @param picFolderUrl - 原始的 pic_folder_url
 * @returns 安全访问链接
 *
 * @example
 * const url = getSafeVisitUrl('https://mft-static.51gonggui.com/wps/file/img/沃尔沃14针诊断口40848')
 * // => 'https://mft-static.51gonggui.com/pdf-loader/index.html?{timestamp}#/?file=https://mft-static.51gonggui.com/wps/file/img/沃尔沃14针诊断口40848'
 */
export function getSafeVisitUrl(picFolderUrl: string): string {
  if (!picFolderUrl) {
    return ''
  }

  try {
    const url = new URL(picFolderUrl)
    const pathParts = url.pathname.split('/')

    // 获取最后一段
    const lastSegment = pathParts[pathParts.length - 1]

    // 分离文件名和扩展名
    let baseName: string
    if (lastSegment.includes('.')) {
      const parts = lastSegment.split('.')
      baseName = parts.slice(0, -1).join('.')
    } else {
      baseName = lastSegment
    }

    // 转换为安全文件名
    const safeBaseName = getSafeFileName(baseName)

    // 替换路径中的文件名
    pathParts[pathParts.length - 1] = lastSegment.replace(baseName, safeBaseName)
    url.pathname = pathParts.join('/')

    const safeUrl = url.toString()

    // 生成带时间戳的最终 URL
    const timestamp = Date.now()
    return `https://mft-static.51gonggui.com/pdf-loader/index.html?${timestamp}#/?file=${safeUrl}`
  } catch (error) {
    console.error('URL 转换失败:', error)
    return ''
  }
}
