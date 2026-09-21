import { describe, expect, it } from 'vitest'
import { downloadFilename } from '../client.js'

// 服务端的下载响应同时带两个名字：ASCII 的 filename（老浏览器兜底）在前，
// RFC 5987 的 filename*（中文名）在后。解析必须取后者。
const STANDARDS_HEADER =
  'attachment; filename="hygiene-standards.zip"; '
  + "filename*=UTF-8''%E6%A0%87%E5%87%86%E5%9B%BE-20260921.zip"

describe('下载文件名解析', () => {
  it('优先用 filename* 里的中文名，而不是前面的 ASCII 兜底名', () => {
    expect(downloadFilename(STANDARDS_HEADER, 'fallback.zip')).toBe('标准图-20260921.zip')
  })

  it('没有 filename* 时用 filename', () => {
    expect(downloadFilename('attachment; filename="backup.zip"', 'x.zip')).toBe('backup.zip')
  })

  it('引号可有可无', () => {
    expect(downloadFilename('attachment; filename=plain.zip', 'x.zip')).toBe('plain.zip')
  })

  it('头缺失或没带名字时回退到兜底名', () => {
    expect(downloadFilename('', 'x.zip')).toBe('x.zip')
    expect(downloadFilename(null, 'x.zip')).toBe('x.zip')
    expect(downloadFilename('attachment', 'x.zip')).toBe('x.zip')
  })

  it('百分号编码不合法时按原文返回，不抛异常', () => {
    expect(downloadFilename("attachment; filename*=UTF-8''%E4%B8", 'x.zip')).toBe('%E4%B8')
  })
})
