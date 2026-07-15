import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { createPortal } from 'react-dom'
import { CornerDownLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import SelectedXlsxPopup from './SelectedXlsxPopup'
import markdownComponents from '../markdown/markdownComponents'
import MermaidDiagram from '../markdown/MermaidDiagram'

const CODE_EXTENSIONS = new Set([
  '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go', '.rs',
  '.rb', '.php', '.c', '.cpp', '.h', '.hpp', '.swift', '.kt',
  '.scala', '.r', '.lua', '.sh', '.sql', '.css',
])

const TEXT_EXTENSIONS = new Set(['.txt', '.log', '.conf', '.ini', '.env', '.dockerfile'])
const STRUCTURED_EXTENSIONS = new Set(['.json', '.yaml', '.yml', '.toml', '.xml'])
const SPREADSHEET_EXTENSIONS = new Set(['.csv', '.xlsx', '.xls'])
const WORD_EXTENSIONS = new Set(['.docx', '.doc'])
const PRESENTATION_EXTENSIONS = new Set(['.pptx'])
const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.avif'])
const SPREADSHEET_MIME_TYPES = new Set([
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'text/csv',
])
const WORD_MIME_TYPES = new Set([
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
])
const PRESENTATION_MIME_TYPES = new Set([
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'application/vnd.ms-powerpoint',
])
const STRUCTURED_MIME_TYPES = new Set([
  'application/json',
  'application/xml',
  'application/yaml',
  'application/x-yaml',
  'text/xml',
  'text/yaml',
])
const ZIP_SIGNATURES = [
  [0x50, 0x4b, 0x03, 0x04],
  [0x50, 0x4b, 0x05, 0x06],
  [0x50, 0x4b, 0x07, 0x08],
]
const OLE_SIGNATURE = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]
const SPREADSHEET_ROW_HEADER_WIDTH = 52
const SPREADSHEET_MIN_COLUMN_WIDTH = 48
const SPREADSHEET_MAX_COLUMN_WIDTH = 360
const SPREADSHEET_DEFAULT_ROW_HEIGHT = 28
const SPREADSHEET_MIN_ROW_HEIGHT = 22
const SPREADSHEET_MAX_ROW_HEIGHT = 160
const SPREADSHEET_HEADER_HEIGHT = 34
const SPREADSHEET_AUTOSCROLL_EDGE_SIZE = 48
const SPREADSHEET_AUTOSCROLL_MAX_STEP = 24
const SPREADSHEET_SELECTED_CELL_BACKGROUND = 'color-mix(in srgb, var(--blue) 10%, var(--bg-base))'
const SPREADSHEET_SELECTED_HEADER_BACKGROUND = 'color-mix(in srgb, var(--blue) 10%, var(--bg-elevated))'
const EXCEL_POINT_TO_PX = 96 / 72
const EXCEL_DEFAULT_COLUMN_WIDTH = 8.43
const EXCEL_THEME_COLORS = ['FFFFFF', '000000', 'EEECE1', '1F497D', '4F81BD', 'C0504D', '9BBB59', '8064A2', '4BACC6', 'F79646']
const EXCEL_INDEXED_COLORS = [
  '000000', 'FFFFFF', 'FF0000', '00FF00', '0000FF', 'FFFF00', 'FF00FF', '00FFFF',
  '000000', 'FFFFFF', 'FF0000', '00FF00', '0000FF', 'FFFF00', 'FF00FF', '00FFFF',
  '800000', '008000', '000080', '808000', '800080', '008080', 'C0C0C0', '808080',
  '9999FF', '993366', 'FFFFCC', 'CCFFFF', '660066', 'FF8080', '0066CC', 'CCCCFF',
  '000080', 'FF00FF', 'FFFF00', '00FFFF', '800080', '800000', '008080', '0000FF',
  '00CCFF', 'CCFFFF', 'CCFFCC', 'FFFF99', '99CCFF', 'FF99CC', 'CC99FF', 'FFCC99',
  '3366FF', '33CCCC', '99CC00', 'FFCC00', 'FF9900', 'FF6600', '666699', '969696',
  '003366', '339966', '003300', '333300', '993300', '993366', '333399', '333333',
]

function clampByte(value) {
  return Math.min(255, Math.max(0, Math.round(value)))
}

function applyTintToChannel(channel, tint) {
  if (!Number.isFinite(tint) || tint === 0) return channel
  if (tint < 0) return clampByte(channel * (1 + tint))
  return clampByte(channel + (255 - channel) * tint)
}

function applyTintToHex(hex, tint) {
  const normalized = String(hex || '').replace(/^#/, '')
  if (!/^[0-9a-f]{6}$/i.test(normalized)) return hex
  const r = parseInt(normalized.slice(0, 2), 16)
  const g = parseInt(normalized.slice(2, 4), 16)
  const b = parseInt(normalized.slice(4, 6), 16)
  return [
    applyTintToChannel(r, tint),
    applyTintToChannel(g, tint),
    applyTintToChannel(b, tint),
  ].map((value) => value.toString(16).padStart(2, '0')).join('').toUpperCase()
}

function excelColorToCss(color, fallback = null) {
  if (!color) return fallback
  let hex = null
  if (color.argb) {
    const value = String(color.argb).replace(/^#/, '')
    hex = value.length === 8 ? value.slice(2) : value
  } else if (color.rgb) {
    hex = String(color.rgb).replace(/^#/, '')
  } else if (Number.isFinite(color.theme)) {
    hex = EXCEL_THEME_COLORS[color.theme] || null
  } else if (Number.isFinite(color.indexed)) {
    hex = EXCEL_INDEXED_COLORS[color.indexed] || null
  }

  if (!hex || !/^[0-9a-f]{6}$/i.test(hex)) return fallback
  return `#${applyTintToHex(hex, color.tint)}`
}

function excelBorderToPreview(edge) {
  if (!edge?.style || edge.style === 'none') return null
  const widthMap = {
    hair: 1,
    thin: 1,
    medium: 2,
    thick: 3,
    double: 3,
  }
  const styleMap = {
    dashed: 'dashed',
    dashDot: 'dashed',
    dashDotDot: 'dashed',
    mediumDashed: 'dashed',
    mediumDashDot: 'dashed',
    mediumDashDotDot: 'dashed',
    dotted: 'dotted',
    double: 'double',
  }
  const width = widthMap[edge.style] || 1
  const lineStyle = styleMap[edge.style] || 'solid'
  const color = excelColorToCss(edge.color, 'var(--border-subtle)')
  return {
    css: `${width}px ${lineStyle} ${color}`,
    color,
    width,
    dashArray: lineStyle === 'dashed' ? '6 4' : lineStyle === 'dotted' ? '2 3' : undefined,
  }
}

function decodeCellAddress(address) {
  const match = String(address || '').match(/^([A-Z]+)(\d+)$/i)
  if (!match) return null
  const letters = match[1].toUpperCase()
  let col = 0
  for (const letter of letters) {
    col = col * 26 + (letter.charCodeAt(0) - 64)
  }
  return { row: Number(match[2]) - 1, col: col - 1 }
}

function decodeCellRange(range) {
  const [start, end] = String(range || '').split(':')
  const startAddress = decodeCellAddress(start)
  const endAddress = decodeCellAddress(end || start)
  if (!startAddress || !endAddress) return null
  return {
    startRow: Math.min(startAddress.row, endAddress.row),
    endRow: Math.max(startAddress.row, endAddress.row),
    startCol: Math.min(startAddress.col, endAddress.col),
    endCol: Math.max(startAddress.col, endAddress.col),
  }
}

function excelColumnWidthToPx(width) {
  const safeWidth = Number.isFinite(width) ? width : EXCEL_DEFAULT_COLUMN_WIDTH
  return Math.round(safeWidth * 7 + 5)
}

function excelRowHeightToPx(height) {
  if (!Number.isFinite(height)) return null
  return Math.round(height * EXCEL_POINT_TO_PX)
}

function getLanguage(ext) {
  const map = {
    '.py': 'python', '.js': 'javascript', '.ts': 'typescript', '.jsx': 'javascript', '.tsx': 'typescript',
    '.java': 'java', '.go': 'go', '.rs': 'rust', '.rb': 'ruby', '.php': 'php',
    '.c': 'c', '.cpp': 'cpp', '.h': 'c', '.hpp': 'cpp', '.swift': 'swift',
    '.kt': 'kotlin', '.scala': 'scala', '.r': 'r', '.lua': 'lua', '.sh': 'bash',
    '.sql': 'sql', '.css': 'css',
    '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml', '.xml': 'xml',
  }
  return map[ext] || 'plaintext'
}

function TextPreview({ content }) {
  return (
    <pre
      style={{
        margin: 0,
        padding: '16px',
        fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
        fontSize: 12,
        color: 'var(--text-secondary)',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        lineHeight: 1.6,
      }}
    >
      {content}
    </pre>
  )
}

function CodePreview({ content, language }) {
  return (
    <div className="overflow-x-auto" style={{ padding: '16px' }}>
      <pre style={{ margin: 0 }}>
        <code
          className={`hljs language-${language}`}
          style={{
            fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
            fontSize: 12,
            lineHeight: 1.6,
            background: 'var(--bg-elevated)',
          }}
        >
          {content}
        </code>
      </pre>
    </div>
  )
}

function MarkdownPreview({ content }) {
  return (
    <div className="markdown-body overflow-hidden px-4 py-3" style={{ wordBreak: 'break-word' }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function MermaidFilePreview({ content }) {
  return (
    <div
      className="overflow-hidden"
      style={{
        width: '100%',
        minWidth: 0,
        height: '100%',
        minHeight: 0,
        padding: '12px 16px',
        boxSizing: 'border-box',
      }}
    >
      <MermaidDiagram code={content || ''} fill />
    </div>
  )
}

function TablePreview({ rows, maxRows = 100 }) {
  if (!rows || rows.length === 0) return null
  const headers = rows[0]
  const body = Number.isFinite(maxRows)
    ? rows.slice(1, maxRows + 1)
    : rows.slice(1)

  return (
    <div className="overflow-x-auto" style={{ padding: '8px' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            {headers.map((header, index) => (
              <th
                key={index}
                className="px-3 py-2 text-xs text-left truncate"
                style={{
                  color: 'var(--text-primary)',
                  borderBottom: '2px solid var(--border)',
                  fontWeight: 600,
                  maxWidth: 200,
                }}
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className="px-3 py-1 text-xs truncate"
                  style={{
                    color: 'var(--text-secondary)',
                    borderBottom: '1px solid var(--border)',
                    maxWidth: 200,
                  }}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {Number.isFinite(maxRows) && rows.length > maxRows + 1 && (
        <div className="text-xs py-2" style={{ color: 'var(--text-dim)', textAlign: 'center' }}>
          Showing {maxRows} of {rows.length - 1} rows
        </div>
      )}
    </div>
  )
}

function getMaxColumnCount(rows) {
  return rows.reduce((max, row) => Math.max(max, Array.isArray(row) ? row.length : 0), 0)
}

function getColumnLabel(index) {
  let value = index + 1
  let label = ''
  while (value > 0) {
    const remainder = (value - 1) % 26
    label = String.fromCharCode(65 + remainder) + label
    value = Math.floor((value - 1) / 26)
  }
  return label
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

function getSheetLayoutKey(sheet, index) {
  return `${index}:${sheet?.name || 'sheet'}`
}

function toCellDisplayValue(value) {
  if (value == null) return ''
  if (typeof value === 'string') return value
  return String(value)
}

function getExcelCellDisplayValue(cell) {
  if (!cell) return ''
  const value = cell.value
  if (cell.text) return cell.text
  if (value == null) return ''
  if (value instanceof Date) return value.toLocaleString()
  if (typeof value !== 'object') return String(value)
  if (Array.isArray(value.richText)) {
    return value.richText.map((part) => part.text || '').join('')
  }
  if (value.text != null) return String(value.text)
  if (value.result != null) return String(value.result)
  if (value.formula) return value.result != null ? String(value.result) : `=${value.formula}`
  if (value.hyperlink && value.text) return String(value.text)
  return String(value)
}

function getExcelTextDecoration(font = {}) {
  const decorations = []
  if (font.underline) decorations.push('underline')
  if (font.strike) decorations.push('line-through')
  return decorations.length ? decorations.join(' ') : undefined
}

function getExcelFontFamily(name) {
  if (!name) return undefined
  const escaped = String(name).replace(/["\\]/g, '')
  return `"${escaped}", "Noto Sans", sans-serif`
}

function getExcelCellStyle(cell) {
  const style = cell?.style || {}
  const font = style.font || {}
  const alignment = style.alignment || {}
  const border = style.border || {}
  const fill = style.fill || {}
  const previewStyle = {}

  const fillColor = fill?.fgColor || fill?.bgColor
  if (fill.type === 'pattern' && fill.pattern !== 'none') {
    const background = excelColorToCss(fillColor)
    if (background) previewStyle.background = background
  }

  const color = excelColorToCss(font.color)
  if (color) previewStyle.color = color
  if (font.bold) previewStyle.fontWeight = 700
  if (font.italic) previewStyle.fontStyle = 'italic'
  if (font.size) previewStyle.fontSize = Math.round(font.size * EXCEL_POINT_TO_PX)
  if (font.name) previewStyle.fontFamily = getExcelFontFamily(font.name)
  const textDecoration = getExcelTextDecoration(font)
  if (textDecoration) previewStyle.textDecoration = textDecoration

  if (alignment.horizontal) previewStyle.horizontal = alignment.horizontal
  if (alignment.vertical) previewStyle.vertical = alignment.vertical
  if (alignment.wrapText) previewStyle.wrapText = true
  if (alignment.textRotation) previewStyle.textRotation = alignment.textRotation

  const top = excelBorderToPreview(border.top)
  const right = excelBorderToPreview(border.right)
  const bottom = excelBorderToPreview(border.bottom)
  const left = excelBorderToPreview(border.left)
  if (top) previewStyle.borderTop = top.css
  if (right) previewStyle.borderRight = right.css
  if (bottom) previewStyle.borderBottom = bottom.css
  if (left) previewStyle.borderLeft = left.css

  const diagonal = excelBorderToPreview(border.diagonal)
  if (diagonal && (border.diagonal?.up || border.diagonal?.down)) {
    previewStyle.diagonal = {
      up: Boolean(border.diagonal.up),
      down: Boolean(border.diagonal.down),
      color: diagonal.color,
      width: diagonal.width,
      dashArray: diagonal.dashArray,
    }
  }

  return Object.keys(previewStyle).length ? previewStyle : null
}

function getSheetMergeMaps(worksheet) {
  const mergedCells = {}
  const hiddenCells = {}
  const ranges = worksheet?.model?.merges || []

  ranges.forEach((rangeText) => {
    const range = decodeCellRange(rangeText)
    if (!range) return
    const rowSpan = range.endRow - range.startRow + 1
    const colSpan = range.endCol - range.startCol + 1
    if (rowSpan <= 1 && colSpan <= 1) return

    mergedCells[`${range.startRow}:${range.startCol}`] = { rowSpan, colSpan }
    for (let rowIndex = range.startRow; rowIndex <= range.endRow; rowIndex += 1) {
      for (let colIndex = range.startCol; colIndex <= range.endCol; colIndex += 1) {
        if (rowIndex !== range.startRow || colIndex !== range.startCol) {
          hiddenCells[`${rowIndex}:${colIndex}`] = true
        }
      }
    }
  })

  return { mergedCells, hiddenCells }
}

function getWorksheetBounds(worksheet) {
  const mergeRanges = worksheet?.model?.merges || []
  let rowCount = Math.max(worksheet?.rowCount || 0, worksheet?.actualRowCount || 0, 1)
  let columnCount = Math.max(worksheet?.columnCount || 0, worksheet?.actualColumnCount || 0, 1)

  worksheet?.eachRow?.({ includeEmpty: true }, (row, rowNumber) => {
    rowCount = Math.max(rowCount, rowNumber)
    columnCount = Math.max(columnCount, row.cellCount || 0, row.actualCellCount || 0)
  })

  mergeRanges.forEach((rangeText) => {
    const range = decodeCellRange(rangeText)
    if (!range) return
    rowCount = Math.max(rowCount, range.endRow + 1)
    columnCount = Math.max(columnCount, range.endCol + 1)
  })

  return { rowCount, columnCount: Math.max(columnCount, 1) }
}

function getPreviewSheetFromExcelWorksheet(worksheet) {
  const { rowCount, columnCount } = getWorksheetBounds(worksheet)
  const rows = Array.from({ length: rowCount }, () => Array.from({ length: columnCount }, () => ''))
  const cellStyles = {}
  const rowHeights = {}
  const columnWidths = {}
  const { mergedCells, hiddenCells } = getSheetMergeMaps(worksheet)
  const defaultRowHeight = excelRowHeightToPx(worksheet?.properties?.defaultRowHeight)

  for (let colIndex = 0; colIndex < columnCount; colIndex += 1) {
    const column = worksheet.getColumn(colIndex + 1)
    if (column?.hidden) {
      columnWidths[colIndex] = 0
    } else if (column?.width) {
      columnWidths[colIndex] = excelColumnWidthToPx(column.width)
    }
  }

  for (let rowIndex = 0; rowIndex < rowCount; rowIndex += 1) {
    const row = worksheet.getRow(rowIndex + 1)
    if (row?.hidden) {
      rowHeights[rowIndex] = 0
    } else if (row?.height) {
      rowHeights[rowIndex] = excelRowHeightToPx(row.height)
    } else if (defaultRowHeight) {
      rowHeights[rowIndex] = defaultRowHeight
    }

    for (let colIndex = 0; colIndex < columnCount; colIndex += 1) {
      const cell = row.getCell(colIndex + 1)
      rows[rowIndex][colIndex] = getExcelCellDisplayValue(cell)
      const previewStyle = getExcelCellStyle(cell)
      if (previewStyle) cellStyles[`${rowIndex}:${colIndex}`] = previewStyle
    }
  }

  return {
    name: worksheet.name,
    rows,
    cellStyles,
    mergedCells,
    hiddenCells,
    columnWidths,
    rowHeights,
  }
}

async function readXlsxWorkbook(buffer) {
  const mod = await import('exceljs/dist/exceljs.min.js')
  const ExcelJS = mod.default || mod
  const workbook = new ExcelJS.Workbook()
  await workbook.xlsx.load(buffer.slice(0))
  const sheets = []
  workbook.eachSheet((worksheet) => {
    sheets.push(getPreviewSheetFromExcelWorksheet(worksheet))
  })
  return sheets
}

function getTextUnits(value) {
  return Array.from(String(value)).reduce((units, char) => {
    if (char === '\t') return units + 2
    return units + (/[^\x00-\xff]/.test(char) ? 1.7 : 1)
  }, 0)
}

function getCellTextLines(value) {
  const text = toCellDisplayValue(value)
  return text.split(/\r\n|\n|\r/)
}

function getPreviewFontSize(style) {
  return Number.isFinite(style?.fontSize) ? style.fontSize : 12
}

function estimateCellTextWidth(value, style = {}) {
  const fontSize = getPreviewFontSize(style)
  const weightRatio = style.fontWeight >= 600 ? 1.08 : 1
  const maxUnits = getCellTextLines(value).reduce((max, line) => Math.max(max, getTextUnits(line.trim())), 0)
  return Math.ceil(maxUnits * fontSize * 0.72 * weightRatio + 24)
}

function estimateWrappedLineCount(line, width, style = {}) {
  const fontSize = getPreviewFontSize(style)
  const availableWidth = Math.max(width - 20, 12)
  const unitsPerLine = Math.max(1, availableWidth / (fontSize * 0.72))
  return Math.max(1, Math.ceil(getTextUnits(line) / unitsPerLine))
}

function getBestFitColumnWidth(columnIndex, rows, cellStyles = {}, hiddenCells = {}, mergedCells = {}) {
  let bestWidth = estimateCellTextWidth(getColumnLabel(columnIndex), { fontWeight: 700 })

  rows.forEach((row, rowIndex) => {
    const key = getCellKey(rowIndex, columnIndex)
    if (hiddenCells[key]) return

    const style = cellStyles[key] || {}
    const merge = mergedCells[key]
    const span = merge?.colSpan || 1
    const value = row?.[columnIndex] ?? ''
    let width = estimateCellTextWidth(value, style)
    if (style.diagonal) width += 28
    if (span > 1) width = Math.ceil(width / span)
    bestWidth = Math.max(bestWidth, width)
  })

  return clamp(bestWidth, SPREADSHEET_MIN_COLUMN_WIDTH, SPREADSHEET_MAX_COLUMN_WIDTH)
}

function getBestFitRowHeight(rowIndex, rows, cellStyles = {}, hiddenCells = {}, mergedCells = {}, columnWidths = []) {
  const row = rows[rowIndex] || []
  let bestHeight = SPREADSHEET_MIN_ROW_HEIGHT

  for (let columnIndex = 0; columnIndex < row.length; columnIndex += 1) {
    const key = getCellKey(rowIndex, columnIndex)
    if (hiddenCells[key]) continue

    const style = cellStyles[key] || {}
    const merge = mergedCells[key]
    const colSpan = merge?.colSpan || 1
    const rowSpan = merge?.rowSpan || 1
    const width = colSpan > 1 ? getSpanWidth(columnIndex, colSpan, columnWidths) : columnWidths[columnIndex]
    const lines = getCellTextLines(row[columnIndex])
    const fontSize = getPreviewFontSize(style)
    const lineCount = style.wrapText
      ? lines.reduce((sum, line) => sum + estimateWrappedLineCount(line, width || SPREADSHEET_MIN_COLUMN_WIDTH, style), 0)
      : Math.max(lines.length, 1)

    let height = Math.ceil(lineCount * (fontSize + 4) + 16)
    if (style.diagonal && lines.length > 1) {
      height = Math.max(height, Math.ceil(fontSize * 2 + 28))
    }
    if (style.textRotation && style.textRotation !== 'vertical') {
      height = Math.max(height, Math.ceil(estimateCellTextWidth(row[columnIndex], style) * 0.6))
    }
    if (rowSpan > 1) height = Math.ceil(height / rowSpan)
    bestHeight = Math.max(bestHeight, height)
  }

  return clamp(bestHeight, SPREADSHEET_MIN_ROW_HEIGHT, SPREADSHEET_MAX_ROW_HEIGHT)
}

function estimateColumnWidths(rows, viewportWidth, columnCount) {
  const sampleRows = rows.slice(0, Math.min(rows.length, 40))
  const estimated = Array.from({ length: columnCount }, (_, columnIndex) => {
    let maxUnits = getTextUnits(getColumnLabel(columnIndex))
    sampleRows.forEach((row) => {
      const cell = toCellDisplayValue(row?.[columnIndex] ?? '')
      cell.split('\n').forEach((line) => {
        maxUnits = Math.max(maxUnits, getTextUnits(line.trim()))
      })
    })
    return clamp(Math.round(maxUnits * 9.5 + 28), SPREADSHEET_MIN_COLUMN_WIDTH, SPREADSHEET_MAX_COLUMN_WIDTH)
  })

  const availableWidth = Math.max((viewportWidth || 0) - SPREADSHEET_ROW_HEADER_WIDTH, 0)
  const estimatedTotal = estimated.reduce((sum, width) => sum + width, 0)

  if (availableWidth > estimatedTotal && columnCount > 0) {
    const extraPerColumn = Math.floor((availableWidth - estimatedTotal) / columnCount)
    return estimated.map((width) => Math.max(SPREADSHEET_MIN_COLUMN_WIDTH, width + extraPerColumn))
  }

  return estimated
}

function normalizeSelectionBounds(bounds) {
  if (!bounds) return null
  return {
    startRowIndex: Math.min(bounds.startRowIndex, bounds.endRowIndex),
    endRowIndex: Math.max(bounds.startRowIndex, bounds.endRowIndex),
    startColIndex: Math.min(bounds.startColIndex, bounds.endColIndex),
    endColIndex: Math.max(bounds.startColIndex, bounds.endColIndex),
    anchorRowIndex: bounds.anchorRowIndex ?? bounds.startRowIndex,
    anchorColIndex: bounds.anchorColIndex ?? bounds.startColIndex,
  }
}

function toRangeA1(bounds) {
  const start = `${getColumnLabel(bounds.startColIndex)}${bounds.startRowIndex + 1}`
  const end = `${getColumnLabel(bounds.endColIndex)}${bounds.endRowIndex + 1}`
  return start === end ? start : `${start}:${end}`
}

function buildSelectionTsv(rows, bounds) {
  const lines = []
  for (let rowIndex = bounds.startRowIndex; rowIndex <= bounds.endRowIndex; rowIndex += 1) {
    const values = []
    for (let colIndex = bounds.startColIndex; colIndex <= bounds.endColIndex; colIndex += 1) {
      values.push(toCellDisplayValue(rows?.[rowIndex]?.[colIndex] ?? ''))
    }
    lines.push(values.join('\t'))
  }
  return lines.join('\n')
}

function buildSpreadsheetSelection(bounds, sheet, sheetIndex) {
  const normalized = normalizeSelectionBounds(bounds)
  if (!normalized) return null
  return {
    sheetIndex,
    sheetName: sheet?.name || '',
    startRowIndex: normalized.startRowIndex,
    endRowIndex: normalized.endRowIndex,
    startColIndex: normalized.startColIndex,
    endColIndex: normalized.endColIndex,
    anchorRowIndex: normalized.anchorRowIndex,
    anchorColIndex: normalized.anchorColIndex,
    rangeA1: toRangeA1(normalized),
    contentTsv: buildSelectionTsv(sheet?.rows || [], normalized),
  }
}

function buildSizeOffsets(sizes) {
  const offsets = []
  let total = 0
  sizes.forEach((size) => {
    offsets.push(total)
    total += size
  })
  return { offsets, total }
}

function getIndexFromOffset(offset, sizes, offsetInfo) {
  if (!sizes.length) return 0
  const safeOffset = clamp(offset, 0, Math.max(offsetInfo.total - 1, 0))
  let low = 0
  let high = sizes.length - 1

  while (low <= high) {
    const mid = Math.floor((low + high) / 2)
    const start = offsetInfo.offsets[mid]
    const end = start + sizes[mid]
    if (safeOffset < start) {
      high = mid - 1
    } else if (safeOffset >= end) {
      low = mid + 1
    } else {
      return mid
    }
  }

  return clamp(low, 0, sizes.length - 1)
}

function getSelectionPointFromMouse(event, viewport, columnWidths, rowHeights, columnOffsetInfo, rowOffsetInfo) {
  const rect = viewport.getBoundingClientRect()
  const contentX = viewport.scrollLeft + event.clientX - rect.left - SPREADSHEET_ROW_HEADER_WIDTH
  const contentY = viewport.scrollTop + event.clientY - rect.top - SPREADSHEET_HEADER_HEIGHT

  return {
    rowIndex: getIndexFromOffset(contentY, rowHeights, rowOffsetInfo),
    columnIndex: getIndexFromOffset(contentX, columnWidths, columnOffsetInfo),
  }
}

function getAutoScrollDelta(event, viewport) {
  const rect = viewport.getBoundingClientRect()
  const getAxisDelta = (value, start, end) => {
    if (value < start + SPREADSHEET_AUTOSCROLL_EDGE_SIZE) {
      const ratio = clamp((start + SPREADSHEET_AUTOSCROLL_EDGE_SIZE - value) / SPREADSHEET_AUTOSCROLL_EDGE_SIZE, 0, 1)
      return -Math.ceil(ratio * SPREADSHEET_AUTOSCROLL_MAX_STEP)
    }
    if (value > end - SPREADSHEET_AUTOSCROLL_EDGE_SIZE) {
      const ratio = clamp((value - (end - SPREADSHEET_AUTOSCROLL_EDGE_SIZE)) / SPREADSHEET_AUTOSCROLL_EDGE_SIZE, 0, 1)
      return Math.ceil(ratio * SPREADSHEET_AUTOSCROLL_MAX_STEP)
    }
    return 0
  }

  return {
    x: getAxisDelta(event.clientX, rect.left, rect.right),
    y: getAxisDelta(event.clientY, rect.top, rect.bottom),
  }
}

function clampSelectionTipPoint(point) {
  if (typeof window === 'undefined') return point
  return {
    x: clamp(point.x, 8, Math.max(window.innerWidth - 160, 8)),
    y: clamp(point.y, 8, Math.max(window.innerHeight - 48, 8)),
  }
}

function getCellSelectionState(rowIndex, columnIndex, selection) {
  if (!selection) {
    return {
      selected: false,
      top: false,
      bottom: false,
      left: false,
      right: false,
      anchor: false,
    }
  }

  const selected = (
    rowIndex >= selection.startRowIndex &&
    rowIndex <= selection.endRowIndex &&
    columnIndex >= selection.startColIndex &&
    columnIndex <= selection.endColIndex
  )

  if (!selected) {
    return {
      selected: false,
      top: false,
      bottom: false,
      left: false,
      right: false,
      anchor: false,
    }
  }

  return {
    selected: true,
    top: rowIndex === selection.startRowIndex,
    bottom: rowIndex === selection.endRowIndex,
    left: columnIndex === selection.startColIndex,
    right: columnIndex === selection.endColIndex,
    anchor: rowIndex === selection.anchorRowIndex && columnIndex === selection.anchorColIndex,
  }
}

function getCellKey(rowIndex, columnIndex) {
  return `${rowIndex}:${columnIndex}`
}

function getHorizontalAlignment(value) {
  if (value === 'center') return { justifyContent: 'center', textAlign: 'center' }
  if (value === 'right') return { justifyContent: 'flex-end', textAlign: 'right' }
  return { justifyContent: 'flex-start', textAlign: 'left' }
}

function getVerticalAlignment(value) {
  if (value === 'middle') return 'center'
  if (value === 'bottom') return 'flex-end'
  return 'flex-start'
}

function getCellTextRotationStyle(style) {
  if (!style?.textRotation) return undefined
  if (style.textRotation === 'vertical') return { writingMode: 'vertical-rl' }
  const rotation = Number(style.textRotation)
  if (!Number.isFinite(rotation) || rotation === 0) return undefined
  return {
    transform: `rotate(${rotation > 90 ? 90 - rotation : -rotation}deg)`,
    transformOrigin: 'center',
  }
}

function getSpanWidth(startColumn, colSpan, columnWidths) {
  let width = 0
  for (let index = startColumn; index < startColumn + colSpan; index += 1) {
    width += columnWidths[index] || 0
  }
  return width
}

function getSpanHeight(startRow, rowSpan, rowHeights) {
  let height = 0
  for (let index = startRow; index < startRow + rowSpan; index += 1) {
    height += rowHeights[index] || 0
  }
  return height
}

function shouldUseDiagonalTextLayout(style, value) {
  if (!style?.diagonal || style.textRotation) return false
  return getCellTextLines(value).filter((line) => line.trim() !== '').length >= 2
}

function DiagonalCellText({ value, style }) {
  const lines = getCellTextLines(value).filter((line) => line.trim() !== '')
  if (lines.length < 2) return null
  const first = lines[0]
  const second = lines.slice(1).join('\n')
  const down = style?.diagonal?.down || !style?.diagonal?.up
  const base = {
    position: 'absolute',
    zIndex: 3,
    maxWidth: '58%',
    overflow: 'hidden',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    lineHeight: 1.25,
    color: style.color || 'var(--text-secondary)',
  }

  return (
    <>
      <span
        style={{
          ...base,
          top: 8,
          ...(down ? { right: 10, textAlign: 'right' } : { left: 10, textAlign: 'left' }),
        }}
      >
        {first}
      </span>
      <span
        style={{
          ...base,
          bottom: 8,
          ...(down ? { left: 10, textAlign: 'left' } : { right: 10, textAlign: 'right' }),
        }}
      >
        {second}
      </span>
    </>
  )
}

function DiagonalCellBorders({ diagonal }) {
  if (!diagonal?.up && !diagonal?.down) return null
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        overflow: 'hidden',
        zIndex: 1,
      }}
    >
      {diagonal.down && (
        <line
          x1="0"
          y1="0"
          x2="100%"
          y2="100%"
          stroke={diagonal.color}
          strokeWidth={diagonal.width}
          strokeDasharray={diagonal.dashArray}
          vectorEffect="non-scaling-stroke"
        />
      )}
      {diagonal.up && (
        <line
          x1="0"
          y1="100%"
          x2="100%"
          y2="0"
          stroke={diagonal.color}
          strokeWidth={diagonal.width}
          strokeDasharray={diagonal.dashArray}
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  )
}

function SpreadsheetGrid({
  rows,
  t,
  cellStyles,
  mergedCells,
  hiddenCells,
  sheetColumnWidths,
  sheetRowHeights,
  columnWidths,
  rowHeights,
  viewportWidth,
  onViewportWidthChange,
  onColumnWidthChange,
  onRowHeightChange,
  selectedRange,
  onSelectionStart,
  onSelectionComplete,
}) {
  const viewportRef = useRef(null)
  const [dragState, setDragState] = useState(null)
  const [selectionDraft, setSelectionDraft] = useState(null)
  const selectionDraftRef = useRef(null)
  const lastSelectionMouseEventRef = useRef(null)
  const autoScrollFrameRef = useRef(null)
  const safeRows = rows || []
  const safeCellStyles = cellStyles || {}
  const safeMergedCells = mergedCells || {}
  const safeHiddenCells = hiddenCells || {}
  const safeSheetColumnWidths = sheetColumnWidths || {}
  const safeSheetRowHeights = sheetRowHeights || {}
  const columnCount = getMaxColumnCount(safeRows)
  const resolvedColumnWidths = useMemo(() => {
    const estimated = estimateColumnWidths(safeRows, viewportWidth, columnCount)
    return estimated.map((width, index) => {
      const userWidth = columnWidths?.[index]
      if (userWidth != null) {
        return clamp(userWidth, SPREADSHEET_MIN_COLUMN_WIDTH, SPREADSHEET_MAX_COLUMN_WIDTH)
      }
      const workbookWidth = safeSheetColumnWidths[index]
      if (workbookWidth != null) {
        return workbookWidth === 0 ? 0 : clamp(workbookWidth, SPREADSHEET_MIN_COLUMN_WIDTH, SPREADSHEET_MAX_COLUMN_WIDTH)
      }
      return clamp(width, SPREADSHEET_MIN_COLUMN_WIDTH, SPREADSHEET_MAX_COLUMN_WIDTH)
    })
  }, [columnCount, columnWidths, safeRows, safeSheetColumnWidths, viewportWidth])
  const resolvedRowHeights = useMemo(
    () => safeRows.map((_, index) => {
      const height = rowHeights?.[index] ?? safeSheetRowHeights[index] ?? SPREADSHEET_DEFAULT_ROW_HEIGHT
      return height === 0 ? 0 : clamp(height, SPREADSHEET_MIN_ROW_HEIGHT, SPREADSHEET_MAX_ROW_HEIGHT)
    }),
    [rowHeights, safeRows, safeSheetRowHeights]
  )
  const tableWidth = useMemo(
    () => SPREADSHEET_ROW_HEADER_WIDTH + resolvedColumnWidths.reduce((sum, width) => sum + width, 0),
    [resolvedColumnWidths]
  )
  const columnOffsetInfo = useMemo(() => buildSizeOffsets(resolvedColumnWidths), [resolvedColumnWidths])
  const rowOffsetInfo = useMemo(() => buildSizeOffsets(resolvedRowHeights), [resolvedRowHeights])

  // Row heights are known exactly, so the virtualizer acts as a pure row-window
  // calculator — no element measurement; spacer rows keep the <table> layout,
  // sticky headers and selection hit-testing untouched.
  const rowVirtualizer = useVirtualizer({
    count: safeRows.length,
    getScrollElement: () => viewportRef.current,
    estimateSize: (index) => resolvedRowHeights[index] ?? SPREADSHEET_DEFAULT_ROW_HEIGHT,
    overscan: 10,
    // tbody rows start below the in-flow (sticky) header row
    scrollMargin: SPREADSHEET_HEADER_HEIGHT,
  })

  // estimateSize is not a dependency of the virtualizer's measurement cache —
  // re-sync when row heights change (drag-resize, sheet switch).
  useEffect(() => {
    rowVirtualizer.measure()
  }, [resolvedRowHeights]) // eslint-disable-line react-hooks/exhaustive-deps
  const committedSelection = useMemo(() => normalizeSelectionBounds(selectedRange), [selectedRange])
  const normalizedDraft = useMemo(() => normalizeSelectionBounds(selectionDraft), [selectionDraft])
  const activeSelection = normalizedDraft || committedSelection

  const updateSelectionFromMouseEvent = useCallback((event) => {
    const viewport = viewportRef.current
    const current = selectionDraftRef.current
    if (!viewport || !current) return

    const point = getSelectionPointFromMouse(
      event,
      viewport,
      resolvedColumnWidths,
      resolvedRowHeights,
      columnOffsetInfo,
      rowOffsetInfo
    )

    const nextDraft = {
      ...current,
      endRowIndex: point.rowIndex,
      endColIndex: point.columnIndex,
    }
    selectionDraftRef.current = nextDraft
    setSelectionDraft(nextDraft)
  }, [columnOffsetInfo, resolvedColumnWidths, resolvedRowHeights, rowOffsetInfo])

  useEffect(() => {
    selectionDraftRef.current = selectionDraft
  }, [selectionDraft])

  useEffect(() => {
    if (typeof onViewportWidthChange !== 'function') return undefined
    const element = viewportRef.current
    if (!element) return undefined

    const updateWidth = () => onViewportWidthChange(Math.round(element.clientWidth))
    updateWidth()

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateWidth)
      return () => window.removeEventListener('resize', updateWidth)
    }

    const observer = new ResizeObserver(() => updateWidth())
    observer.observe(element)
    return () => observer.disconnect()
  }, [onViewportWidthChange])

  useEffect(() => {
    if (!dragState) return undefined

    const onMouseMove = (event) => {
      if (dragState.type === 'column') {
        const nextWidth = clamp(
          dragState.startSize + (event.clientX - dragState.startClient),
          SPREADSHEET_MIN_COLUMN_WIDTH,
          SPREADSHEET_MAX_COLUMN_WIDTH
        )
        onColumnWidthChange?.(dragState.index, nextWidth)
        return
      }

      const nextHeight = clamp(
        dragState.startSize + (event.clientY - dragState.startClient),
        SPREADSHEET_MIN_ROW_HEIGHT,
        SPREADSHEET_MAX_ROW_HEIGHT
      )
      onRowHeightChange?.(dragState.index, nextHeight)
    }

    const onMouseUp = () => setDragState(null)

    document.body.style.cursor = dragState.type === 'column' ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)

    return () => {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
  }, [dragState, onColumnWidthChange, onRowHeightChange])

  useEffect(() => {
    if (!selectionDraft) return undefined

    const stopAutoScroll = () => {
      if (!autoScrollFrameRef.current) return
      cancelAnimationFrame(autoScrollFrameRef.current)
      autoScrollFrameRef.current = null
    }

    const runAutoScroll = () => {
      const viewport = viewportRef.current
      const event = lastSelectionMouseEventRef.current
      if (!viewport || !event || !selectionDraftRef.current) {
        stopAutoScroll()
        return
      }

      const delta = getAutoScrollDelta(event, viewport)
      if (delta.x || delta.y) {
        const beforeLeft = viewport.scrollLeft
        const beforeTop = viewport.scrollTop
        viewport.scrollBy(delta.x, delta.y)
        updateSelectionFromMouseEvent(event)
        if (viewport.scrollLeft !== beforeLeft || viewport.scrollTop !== beforeTop) {
          autoScrollFrameRef.current = requestAnimationFrame(runAutoScroll)
        } else {
          stopAutoScroll()
        }
      } else {
        stopAutoScroll()
      }
    }

    const scheduleAutoScroll = () => {
      if (autoScrollFrameRef.current) return
      autoScrollFrameRef.current = requestAnimationFrame(runAutoScroll)
    }

    const onMouseMove = (event) => {
      if (!(event.buttons & 1)) return
      lastSelectionMouseEventRef.current = event
      updateSelectionFromMouseEvent(event)
      const viewport = viewportRef.current
      if (!viewport) return
      const delta = getAutoScrollDelta(event, viewport)
      if (delta.x || delta.y) scheduleAutoScroll()
      else stopAutoScroll()
    }

    const onMouseUp = (event) => {
      const current = selectionDraftRef.current
      setSelectionDraft(null)
      stopAutoScroll()
      lastSelectionMouseEventRef.current = null
      selectionDraftRef.current = null
      if (!current) return
      const normalized = normalizeSelectionBounds(current)
      if (!normalized) return
      onSelectionComplete?.(normalized, clampSelectionTipPoint({ x: event.clientX + 8, y: event.clientY + 8 }))
    }

    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)

    return () => {
      stopAutoScroll()
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
  }, [onSelectionComplete, selectionDraft, updateSelectionFromMouseEvent])

  if (!safeRows.length) {
    return (
      <div className="p-4 text-xs" style={{ color: 'var(--text-dim)' }}>
        {t('userData.emptySheet')}
      </div>
    )
  }

  // item.start/end include scrollMargin; getTotalSize() excludes it.
  const virtualRows = rowVirtualizer.getVirtualItems()
  const spacerTop = virtualRows.length > 0
    ? virtualRows[0].start - SPREADSHEET_HEADER_HEIGHT
    : 0
  const spacerBottom = virtualRows.length > 0
    ? rowVirtualizer.getTotalSize() - (virtualRows[virtualRows.length - 1].end - SPREADSHEET_HEADER_HEIGHT)
    : 0

  return (
    <div
      ref={viewportRef}
      className="overflow-auto"
      style={{
        flex: 1,
        minHeight: 0,
        height: '100%',
        position: 'relative',
        background: 'var(--bg-base)',
        overflowX: 'auto',
        overflowY: 'auto',
        overscrollBehavior: 'contain',
      }}
    >
      <table
        style={{
          width: Math.max(tableWidth, viewportWidth || 0),
          minWidth: '100%',
          borderCollapse: 'separate',
          borderSpacing: 0,
          fontSize: 12,
          tableLayout: 'fixed',
        }}
      >
        <colgroup>
          <col style={{ width: SPREADSHEET_ROW_HEADER_WIDTH }} />
          {resolvedColumnWidths.map((width, index) => (
            <col key={index} style={{ width }} />
          ))}
        </colgroup>
        <thead>
          <tr>
            <th
              style={{
                position: 'sticky',
                top: 0,
                left: 0,
                zIndex: 4,
                width: SPREADSHEET_ROW_HEADER_WIDTH,
                minWidth: SPREADSHEET_ROW_HEADER_WIDTH,
                height: SPREADSHEET_HEADER_HEIGHT,
                background: 'var(--bg-elevated)',
                borderBottom: '1px solid var(--border)',
                borderRight: '1px solid var(--border-subtle)',
                borderTop: '1px solid var(--border-subtle)',
                borderLeft: '1px solid var(--border-subtle)',
              }}
            />
            {resolvedColumnWidths.map((width, index) => (
              <th
                key={index}
                style={{
                  position: 'sticky',
                  top: 0,
                  zIndex: 3,
                  color: 'var(--text-primary)',
                  background: activeSelection && index >= activeSelection.startColIndex && index <= activeSelection.endColIndex
                    ? SPREADSHEET_SELECTED_HEADER_BACKGROUND
                    : 'var(--bg-elevated)',
                  borderBottom: '1px solid var(--border)',
                  borderRight: '1px solid var(--border-subtle)',
                  borderTop: '1px solid var(--border-subtle)',
                  fontWeight: 600,
                  width,
                  minWidth: width,
                  height: SPREADSHEET_HEADER_HEIGHT,
                  padding: 0,
                  textAlign: 'center',
                }}
              >
                <div
                  style={{
                    position: 'relative',
                    height: SPREADSHEET_HEADER_HEIGHT,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '0 14px',
                  }}
                >
	                  {getColumnLabel(index)}
	                  <div
	                    onDoubleClick={(event) => {
	                      event.preventDefault()
	                      event.stopPropagation()
	                      onColumnWidthChange?.(
	                        index,
	                        getBestFitColumnWidth(index, safeRows, safeCellStyles, safeHiddenCells, safeMergedCells)
	                      )
	                    }}
	                    onMouseDown={(event) => {
	                      event.preventDefault()
	                      event.stopPropagation()
	                      if (event.detail > 1) return
	                      setDragState({
	                        type: 'column',
	                        index,
                        startClient: event.clientX,
                        startSize: width,
                      })
                    }}
                    style={{
                      position: 'absolute',
                      top: 0,
                      right: -3,
                      width: 6,
                      height: '100%',
                      cursor: 'col-resize',
                      zIndex: 6,
                    }}
                  />
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {spacerTop > 0 && (
            <tr aria-hidden="true" style={{ height: spacerTop }}>
              <td colSpan={columnCount + 1} style={{ padding: 0 }} />
            </tr>
          )}
          {virtualRows.map((vi) => {
            const rowIndex = vi.index
            const row = safeRows[rowIndex]
            return (
            <tr key={rowIndex} style={{ height: resolvedRowHeights[rowIndex] }}>
              <th
                scope="row"
                style={{
                  position: 'sticky',
                  left: 0,
                  zIndex: 2,
                  color: 'var(--text-dim)',
                  background: activeSelection && rowIndex >= activeSelection.startRowIndex && rowIndex <= activeSelection.endRowIndex
                    ? SPREADSHEET_SELECTED_HEADER_BACKGROUND
                    : 'var(--bg-elevated)',
                  borderBottom: '1px solid var(--border-subtle)',
                  borderRight: '1px solid var(--border-subtle)',
                  fontWeight: 600,
                  width: SPREADSHEET_ROW_HEADER_WIDTH,
                  minWidth: SPREADSHEET_ROW_HEADER_WIDTH,
                  padding: 0,
                  verticalAlign: 'top',
                }}
              >
                <div
                  style={{
                    position: 'relative',
                    height: resolvedRowHeights[rowIndex],
	                    display: 'flex',
	                    alignItems: 'flex-start',
	                    justifyContent: 'center',
	                    paddingTop: resolvedRowHeights[rowIndex] === 0 ? 0 : 10,
	                  }}
                >
	                  {rowIndex + 1}
	                  <div
	                    onDoubleClick={(event) => {
	                      event.preventDefault()
	                      event.stopPropagation()
	                      onRowHeightChange?.(
	                        rowIndex,
	                        getBestFitRowHeight(rowIndex, safeRows, safeCellStyles, safeHiddenCells, safeMergedCells, resolvedColumnWidths)
	                      )
	                    }}
	                    onMouseDown={(event) => {
	                      event.preventDefault()
	                      event.stopPropagation()
	                      if (event.detail > 1) return
	                      setDragState({
	                        type: 'row',
	                        index: rowIndex,
                        startClient: event.clientY,
                        startSize: resolvedRowHeights[rowIndex],
                      })
                    }}
                    style={{
                      position: 'absolute',
                      left: 0,
                      right: 0,
                      bottom: -3,
                      height: 6,
                      cursor: 'row-resize',
                      zIndex: 6,
                    }}
                  />
                </div>
              </th>
              {resolvedColumnWidths.map((width, columnIndex) => {
                const cellKey = getCellKey(rowIndex, columnIndex)
                if (safeHiddenCells[cellKey]) return null
                const cellStyle = safeCellStyles[cellKey] || {}
                const merge = safeMergedCells[cellKey]
                const colSpan = merge?.colSpan || 1
                const rowSpan = merge?.rowSpan || 1
                const cellWidth = colSpan > 1 ? getSpanWidth(columnIndex, colSpan, resolvedColumnWidths) : width
                const cellHeight = rowSpan > 1 ? getSpanHeight(rowIndex, rowSpan, resolvedRowHeights) : resolvedRowHeights[rowIndex]
                const displayValue = toCellDisplayValue(row?.[columnIndex] ?? '')
	                const selectionState = getCellSelectionState(rowIndex, columnIndex, activeSelection)
	                const horizontal = getHorizontalAlignment(cellStyle.horizontal)
	                const rotationStyle = getCellTextRotationStyle(cellStyle)
	                const whiteSpace = cellStyle.wrapText ? 'pre-wrap' : 'nowrap'
	                const useDiagonalTextLayout = shouldUseDiagonalTextLayout(cellStyle, displayValue)
	                return (
	                  <td
                    key={columnIndex}
                    rowSpan={rowSpan}
                    colSpan={colSpan}
                    onMouseDown={(event) => {
                      if (event.button !== 0 || dragState) return
                      event.preventDefault()
                      lastSelectionMouseEventRef.current = event
                      onSelectionStart?.()
                      const nextDraft = {
                        startRowIndex: rowIndex,
                        endRowIndex: rowIndex,
                        startColIndex: columnIndex,
                        endColIndex: columnIndex,
                        anchorRowIndex: rowIndex,
                        anchorColIndex: columnIndex,
                      }
                      selectionDraftRef.current = nextDraft
                      setSelectionDraft(nextDraft)
                    }}
                    onMouseEnter={(event) => {
                      if (dragState || !(event.buttons & 1)) return
                      lastSelectionMouseEventRef.current = event
                      const current = selectionDraftRef.current
                      if (!current) return
                      const nextDraft = {
                        ...current,
                        endRowIndex: rowIndex,
                        endColIndex: columnIndex,
                      }
                      selectionDraftRef.current = nextDraft
                      setSelectionDraft(nextDraft)
                    }}
	                    style={{
	                      color: cellStyle.color || 'var(--text-secondary)',
	                      borderTop: cellStyle.borderTop || 'none',
	                      borderBottom: cellStyle.borderBottom || '1px solid var(--border-subtle)',
	                      borderLeft: cellStyle.borderLeft || 'none',
	                      borderRight: cellStyle.borderRight || '1px solid var(--border-subtle)',
	                      width: cellWidth,
	                      minWidth: cellWidth,
	                      verticalAlign: 'top',
	                      padding: 0,
	                      background: cellStyle.background || 'var(--bg-base)',
	                      cursor: 'cell',
	                    }}
	                  >
	                    <div
	                      style={{
		                        position: 'relative',
		                        minHeight: cellHeight,
		                        padding: cellHeight === 0 || cellWidth === 0 || useDiagonalTextLayout ? 0 : '8px 10px',
		                        overflow: 'hidden',
		                        whiteSpace,
		                        wordBreak: cellStyle.wrapText ? 'break-word' : 'normal',
	                        display: 'flex',
	                        alignItems: getVerticalAlignment(cellStyle.vertical),
	                        justifyContent: horizontal.justifyContent,
	                        textAlign: horizontal.textAlign,
	                        background: selectionState.selected ? SPREADSHEET_SELECTED_CELL_BACKGROUND : (cellStyle.background || 'var(--bg-base)'),
	                        fontFamily: cellStyle.fontFamily,
	                        fontSize: cellStyle.fontSize,
	                        fontStyle: cellStyle.fontStyle,
	                        fontWeight: cellStyle.fontWeight,
	                        textDecoration: cellStyle.textDecoration,
	                      }}
	                    >
	                      <DiagonalCellBorders diagonal={cellStyle.diagonal} />
	                      {selectionState.selected && (
	                        <span
	                          style={{
                            position: 'absolute',
                            inset: 0,
                            pointerEvents: 'none',
	                            borderTop: selectionState.top ? '2px solid var(--blue)' : 'none',
	                            borderBottom: selectionState.bottom ? '2px solid var(--blue)' : 'none',
	                            borderLeft: selectionState.left ? '2px solid var(--blue)' : 'none',
	                            borderRight: selectionState.right ? '2px solid var(--blue)' : 'none',
	                            background: selectionState.anchor ? 'color-mix(in srgb, var(--blue) 6%, transparent)' : 'transparent',
	                            zIndex: 2,
	                          }}
	                        />
		                      )}
		                      {useDiagonalTextLayout ? (
		                        <DiagonalCellText value={displayValue} style={cellStyle} />
		                      ) : (
		                        <span
		                          style={{
		                            minWidth: 0,
		                            maxWidth: '100%',
		                            overflow: 'hidden',
		                            textOverflow: cellStyle.wrapText ? 'clip' : 'ellipsis',
		                            position: 'relative',
		                            zIndex: 3,
		                            ...rotationStyle,
		                          }}
		                        >
		                          {displayValue}
		                        </span>
		                      )}
	                      {selectionState.anchor && (
	                        <span
	                          style={{
	                            position: 'absolute',
	                            right: 2,
                            bottom: 2,
                            width: 6,
	                            height: 6,
	                            background: 'var(--blue)',
	                            borderRadius: 1,
	                            zIndex: 4,
	                          }}
	                        />
	                      )}
                    </div>
                  </td>
                )
              })}
            </tr>
            )
          })}
          {spacerBottom > 0 && (
            <tr aria-hidden="true" style={{ height: spacerBottom }}>
              <td colSpan={columnCount + 1} style={{ padding: 0 }} />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function WorkbookPreview({ sheets, activeSheetIndex, onSelectSheet, t, filePath }) {
  const safeIndex = Math.min(Math.max(activeSheetIndex, 0), Math.max(sheets.length - 1, 0))
  const activeSheet = sheets[safeIndex]
  const activeSheetKey = getSheetLayoutKey(activeSheet, safeIndex)
  const rowCount = activeSheet?.rows?.length || 0
  const columnCount = getMaxColumnCount(activeSheet?.rows || [])
  const [sheetLayouts, setSheetLayouts] = useState({})
  const [selectedRange, setSelectedRange] = useState(null)
  const [selectionTip, setSelectionTip] = useState(null)
  const [selectionPopupData, setSelectionPopupData] = useState(null)

  useEffect(() => {
    setSheetLayouts((prev) => {
      const next = {}
      sheets.forEach((sheet, index) => {
        const key = getSheetLayoutKey(sheet, index)
        if (prev[key]) next[key] = prev[key]
      })
      return next
    })
  }, [sheets])

  const clearSelectionUi = useCallback(() => {
    setSelectedRange(null)
    setSelectionTip(null)
    setSelectionPopupData(null)
  }, [])

  useEffect(() => {
    clearSelectionUi()
  }, [clearSelectionUi, filePath, safeIndex, sheets])

  useEffect(() => {
    if (!selectionTip) return undefined

    const onMouseDown = (event) => {
      const target = event.target
      if (target?.closest?.('[data-xlsx-selection-tip="true"]')) return
      setSelectionTip(null)
    }

    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [selectionTip])

  const activeLayout = sheetLayouts[activeSheetKey] || {}
  const handleViewportWidthChange = useCallback((width) => {
    setSheetLayouts((prev) => {
      const current = prev[activeSheetKey] || {}
      if (current.viewportWidth === width) return prev
      return {
        ...prev,
        [activeSheetKey]: {
          ...current,
          viewportWidth: width,
        },
      }
    })
  }, [activeSheetKey])

  const handleColumnWidthChange = useCallback((columnIndex, width) => {
    setSheetLayouts((prev) => {
      const current = prev[activeSheetKey] || {}
      const currentWidths = current.columnWidths || {}
      if (currentWidths[columnIndex] === width) return prev
      return {
        ...prev,
        [activeSheetKey]: {
          ...current,
          columnWidths: {
            ...currentWidths,
            [columnIndex]: width,
          },
        },
      }
    })
  }, [activeSheetKey])

  const handleRowHeightChange = useCallback((rowIndex, height) => {
    setSheetLayouts((prev) => {
      const current = prev[activeSheetKey] || {}
      const currentHeights = current.rowHeights || {}
      if (currentHeights[rowIndex] === height) return prev
      return {
        ...prev,
        [activeSheetKey]: {
          ...current,
          rowHeights: {
            ...currentHeights,
            [rowIndex]: height,
          },
        },
      }
    })
  }, [activeSheetKey])

  const handleSelectionStart = useCallback(() => {
    setSelectionTip(null)
    setSelectionPopupData(null)
  }, [])

  const handleSelectionComplete = useCallback((bounds, anchorPoint) => {
    const nextSelection = buildSpreadsheetSelection(bounds, activeSheet, safeIndex)
    if (!nextSelection) return
    setSelectedRange(nextSelection)
    setSelectionTip({
      x: anchorPoint.x,
      y: anchorPoint.y,
      selection: nextSelection,
    })
  }, [activeSheet, safeIndex])

  const handleSelectionTipClick = useCallback(() => {
    if (!selectionTip?.selection) return
    setSelectionPopupData({
      filePath,
      sheetIndex: selectionTip.selection.sheetIndex,
      sheetName: selectionTip.selection.sheetName,
      range: selectionTip.selection.rangeA1,
      contentTsv: selectionTip.selection.contentTsv,
      anchorX: selectionTip.x,
      anchorY: selectionTip.y,
    })
    setSelectionTip(null)
  }, [filePath, selectionTip])

  return (
    <div className="flex flex-col" style={{ height: '100%', minHeight: 0 }}>
      <div style={{ flex: 1, minHeight: 0, padding: '12px', overflow: 'hidden' }}>
        <div
          className="flex flex-col"
          style={{
            border: '1px solid var(--border-subtle)',
            borderRadius: 4,
            overflow: 'hidden',
            background: 'var(--bg-base)',
            height: '100%',
            minHeight: 0,
          }}
        >
          <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex' }}>
	            <SpreadsheetGrid
	              rows={activeSheet?.rows || []}
	              t={t}
	              cellStyles={activeSheet?.cellStyles || {}}
	              mergedCells={activeSheet?.mergedCells || {}}
	              hiddenCells={activeSheet?.hiddenCells || {}}
	              sheetColumnWidths={activeSheet?.columnWidths || {}}
	              sheetRowHeights={activeSheet?.rowHeights || {}}
	              columnWidths={activeLayout.columnWidths || {}}
	              rowHeights={activeLayout.rowHeights || {}}
	              viewportWidth={activeLayout.viewportWidth || 0}
              onViewportWidthChange={handleViewportWidthChange}
              onColumnWidthChange={handleColumnWidthChange}
              onRowHeightChange={handleRowHeightChange}
              selectedRange={selectedRange}
              onSelectionStart={handleSelectionStart}
              onSelectionComplete={handleSelectionComplete}
            />
          </div>
          <div
            className="flex justify-between gap-4 px-3"
            style={{
              borderTop: '1px solid var(--border-subtle)',
              background: 'var(--bg-elevated)',
              minHeight: 40,
              alignItems: 'stretch',
            }}
          >
            <div
              className="flex overflow-x-auto"
              style={{ flex: 1, minWidth: 0, minHeight: 40 }}
            >
              {sheets.map((sheet, index) => {
                const isActive = index === safeIndex
                return (
                  <button
                    key={`${sheet.name}-${index}`}
                    type="button"
                    className="text-xs"
                    onClick={() => onSelectSheet(index)}
                    onMouseEnter={(event) => {
                      if (!isActive) {
                        event.currentTarget.style.background = 'var(--bg-base)'
                        event.currentTarget.style.color = 'var(--text-secondary)'
                      }
                    }}
                    onMouseLeave={(event) => {
                      if (!isActive) {
                        event.currentTarget.style.background = 'transparent'
                        event.currentTarget.style.color = 'var(--text-secondary)'
                      }
                    }}
                    style={{
                      border: 'none',
                      borderTop: isActive ? '2px solid var(--blue)' : '2px solid transparent',
                      borderRight: '1px solid var(--border-subtle)',
                      borderLeft: index === 0 ? '1px solid var(--border-subtle)' : 'none',
                      color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                      background: isActive ? 'var(--bg-base)' : 'transparent',
                      fontWeight: isActive ? 600 : 450,
                      whiteSpace: 'nowrap',
                      height: 40,
                      padding: '0 12px',
                      display: 'flex',
                      alignItems: 'center',
                      transition: 'background 150ms ease, color 150ms ease',
                      fontSize: 13,
                      cursor: 'pointer',
                    }}
                  >
                    {sheet.name || t('userData.unknownSheet')}
                  </button>
                )
              })}
            </div>
            <div
              className="text-xs"
              style={{
                color: 'var(--text-dim)',
                whiteSpace: 'nowrap',
                minHeight: 40,
                display: 'flex',
                alignItems: 'center',
                flexShrink: 0,
              }}
            >
              {t('userData.sheetSummary', {
                current: safeIndex + 1,
                total: sheets.length,
                rows: rowCount,
                columns: columnCount,
              })}
            </div>
          </div>
        </div>
      </div>
      {selectionTip && createPortal(
        <button
          type="button"
          data-xlsx-selection-tip="true"
          className="flex items-center gap-1"
          onClick={handleSelectionTipClick}
          style={{
            position: 'fixed',
            left: selectionTip.x,
            top: selectionTip.y,
            zIndex: 9999,
            background: 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 4,
            padding: '4px 10px',
            cursor: 'pointer',
            fontSize: 12,
            color: 'var(--text-secondary)',
            whiteSpace: 'nowrap',
            transition: 'color 150ms ease, border-color 150ms ease',
          }}
          onMouseEnter={(event) => {
            event.currentTarget.style.color = 'var(--text-primary)'
            event.currentTarget.style.borderColor = 'var(--blue)'
          }}
          onMouseLeave={(event) => {
            event.currentTarget.style.color = 'var(--text-secondary)'
            event.currentTarget.style.borderColor = 'var(--border)'
          }}
        >
          <CornerDownLeft size={14} strokeWidth={1.5} />
          {t('quote.provideFeedback')}
        </button>,
        document.body
      )}
      {selectionPopupData && (
        <SelectedXlsxPopup
          data={selectionPopupData}
          onClose={() => setSelectionPopupData(null)}
        />
      )}
    </div>
  )
}

const PPTX_PREVIEW_MIN_WIDTH = 320
const PPTX_PREVIEW_RESIZE_DEBOUNCE_MS = 180
const PPTX_PREVIEW_RERENDER_THRESHOLD = 48

async function normalizePptxArchiveForPreview(buffer) {
  try {
    const mod = await import('jszip')
    const JSZip = mod.default || mod
    const zip = await JSZip.loadAsync(buffer.slice(0))
    const contentTypesFile = zip.file('[Content_Types].xml')
    if (!contentTypesFile || typeof DOMParser === 'undefined' || typeof XMLSerializer === 'undefined') {
      return buffer
    }

    const xml = await contentTypesFile.async('text')
    const doc = new DOMParser().parseFromString(xml, 'application/xml')
    if (doc.getElementsByTagName('parsererror').length > 0) return buffer

    const overrides = Array.from(doc.getElementsByTagName('Override'))
    let removed = false
    overrides.forEach((node) => {
      const partName = node.getAttribute('PartName') || ''
      const zipPath = partName.replace(/^\/+/, '')
      if (zipPath && !zip.file(zipPath)) {
        node.parentNode?.removeChild(node)
        removed = true
      }
    })

    if (!removed) return buffer
    zip.file('[Content_Types].xml', new XMLSerializer().serializeToString(doc))
    return zip.generateAsync({ type: 'arraybuffer' })
  } catch {
    return buffer
  }
}

function PptxPreview({ buffer }) {
  const viewportRef = useRef(null)
  const containerRef = useRef(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!buffer) return undefined
    const viewport = viewportRef.current
    const el = containerRef.current
    if (!viewport || !el) return undefined

    let cancelled = false
    let previewer = null
    let renderedWidth = 0
    let pendingTargetWidth = 0
    let renderInFlight = false
    let queuedRenderWidth = null
    let resizeTimer = null
    let settleTimer = null
    let resizeObserver = null
    let frameId = null
    const previewBufferPromise = normalizePptxArchiveForPreview(buffer)

    const applyVisualWidth = (width) => {
      const wrapper = el.querySelector('.pptx-preview-wrapper')
      if (!wrapper) return
      const baseWidth = renderedWidth || width
      const scale = width / baseWidth
      const baseHeight = wrapper.scrollHeight || wrapper.getBoundingClientRect().height || Math.round((baseWidth * 9) / 16)

      pendingTargetWidth = width
      el.style.setProperty('width', `${width}px`)
      el.style.setProperty('max-width', '100%')
      el.style.setProperty('height', `${Math.ceil(baseHeight * scale)}px`)
      el.style.setProperty('transition', 'height 120ms cubic-bezier(0.16, 1, 0.3, 1)')

      wrapper.style.setProperty('transform', `scale(${scale})`)
      wrapper.style.setProperty('transition', 'transform 120ms cubic-bezier(0.16, 1, 0.3, 1)')
    }

    const normalizeRenderedLayout = (width) => {
      const wrapper = el.querySelector('.pptx-preview-wrapper')
      if (!wrapper) return
      renderedWidth = width
      wrapper.style.setProperty('background', 'transparent')
      wrapper.style.setProperty('width', `${width}px`)
      wrapper.style.setProperty('min-width', `${width}px`)
      wrapper.style.setProperty('height', 'auto')
      wrapper.style.setProperty('overflow', 'visible')
      wrapper.style.setProperty('overflow-y', 'visible')
      wrapper.style.setProperty('max-width', `${width}px`)

      el.querySelectorAll('.pptx-preview-slide-wrapper').forEach((slide) => {
        slide.style.setProperty('width', `${width}px`)
        slide.style.setProperty('outline', '1px solid var(--border-subtle)')
        slide.style.setProperty('margin', '0 auto 12px')
      })

      applyVisualWidth(pendingTargetWidth || width)
    }

    const renderAt = async (width) => {
      if (renderInFlight) {
        queuedRenderWidth = width
        return
      }
      renderInFlight = true
      try {
        setError(null)
        const mod = await import('pptx-preview')
        if (cancelled) return
        const init = mod.init || mod.default?.init || mod.default
        if (typeof init !== 'function') {
          throw new Error('pptx-preview did not expose init()')
        }
        try {
          previewer?.destroy?.()
        } catch {
          /* ignore */
        }
        const previewBuffer = await previewBufferPromise
        if (cancelled) return
        el.innerHTML = ''
        previewer = init(el, { width, mode: 'list' })
        const result = previewer.preview(previewBuffer.slice(0))
        if (result && typeof result.then === 'function') await result
        if (!cancelled) normalizeRenderedLayout(width)
      } catch (err) {
        if (!cancelled) setError(err?.message || String(err))
      } finally {
        renderInFlight = false
        if (!cancelled && queuedRenderWidth != null) {
          const queued = queuedRenderWidth
          queuedRenderWidth = null
          if (Math.abs(queued - renderedWidth) >= 4) renderAt(queued)
          else applyVisualWidth(queued)
        }
      }
    }

    const getTargetWidth = () => {
      const viewportRect = viewport.getBoundingClientRect()
      const style = window.getComputedStyle(viewport)
      const horizontalPadding = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0)
      const parentWidth = viewport.parentElement?.clientWidth || viewport.offsetParent?.clientWidth || 0
      const measuredWidth = Math.max(viewport.clientWidth || 0, viewportRect.width || 0, parentWidth)
      const available = measuredWidth - horizontalPadding
      return Math.max(PPTX_PREVIEW_MIN_WIDTH, Math.floor(available))
    }

    const renderCurrentWidth = () => {
      if (cancelled) return
      const next = getTargetWidth()
      renderAt(next)
    }

    const syncVisualWidth = () => {
      if (cancelled) return
      const next = getTargetWidth()
      pendingTargetWidth = next
      applyVisualWidth(next)
      if (resizeTimer) clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => {
        if (cancelled) return
        const settledWidth = getTargetWidth()
        pendingTargetWidth = settledWidth
        applyVisualWidth(settledWidth)
        if (!renderedWidth || Math.abs(settledWidth - renderedWidth) >= PPTX_PREVIEW_RERENDER_THRESHOLD) {
          renderAt(settledWidth)
        }
      }, PPTX_PREVIEW_RESIZE_DEBOUNCE_MS)
    }

    frameId = requestAnimationFrame(() => {
      renderCurrentWidth()
      settleTimer = setTimeout(syncVisualWidth, 80)
    })

    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(() => {
        if (cancelled) return
        syncVisualWidth()
      })
      resizeObserver.observe(viewport)
    }

    return () => {
      cancelled = true
      if (frameId) cancelAnimationFrame(frameId)
      if (settleTimer) clearTimeout(settleTimer)
      if (resizeTimer) clearTimeout(resizeTimer)
      if (resizeObserver) resizeObserver.disconnect()
      try {
        previewer?.destroy?.()
      } catch {
        /* ignore */
      }
      if (el) el.innerHTML = ''
    }
  }, [buffer])

  if (error) {
    return (
      <div className="p-4 text-xs" style={{ color: 'var(--red)' }}>
        {error}
      </div>
    )
  }

  return (
    <div
      ref={viewportRef}
      className="overflow-auto"
      style={{
        width: '100%',
        minWidth: 0,
        height: '100%',
        flex: 1,
        padding: 12,
        boxSizing: 'border-box',
        background: 'var(--bg-base)',
      }}
    >
      <div
        ref={containerRef}
        className="pptx-preview-host"
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          width: '100%',
          minWidth: 0,
        }}
      />
    </div>
  )
}

function HtmlPreview({ content }) {
  return (
    <iframe
      title="HTML preview"
      sandbox=""
      srcDoc={content}
      style={{
        width: '100%',
        height: '100%',
        minHeight: 500,
        border: 'none',
        background: 'white',
      }}
    />
  )
}

function ImagePreview({ src, alt }) {
  return (
    <div
      className="flex items-center justify-center"
      style={{
        width: '100%',
        height: '100%',
        minHeight: 320,
        padding: 16,
        background: 'var(--bg-base)',
      }}
    >
      <img
        src={src}
        alt={alt}
        style={{
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
          borderRadius: 4,
          outline: '1px solid var(--border-subtle)',
          background: 'white',
        }}
      />
    </div>
  )
}

function getFileExtension(file) {
  if (file.ext) {
    const ext = String(file.ext).trim().toLowerCase()
    return ext && !ext.startsWith('.') ? `.${ext}` : ext
  }
  const name = file.name || file.original_name || ''
  const idx = name.lastIndexOf('.')
  return idx >= 0 ? name.slice(idx).toLowerCase() : ''
}

function getMimeType(file) {
  return String(file.mime_type || file.mimeType || '').toLowerCase()
}

function inferPreviewKind(file, ext) {
  const mimeType = getMimeType(file)

  if (ext === '.csv' || ext === '.tsv' || mimeType === 'text/csv' || mimeType === 'text/tab-separated-values') return 'csv'
  if (SPREADSHEET_EXTENSIONS.has(ext) || SPREADSHEET_MIME_TYPES.has(mimeType) || mimeType.includes('spreadsheetml')) {
    return 'spreadsheet'
  }
  if (WORD_EXTENSIONS.has(ext) || WORD_MIME_TYPES.has(mimeType) || mimeType.includes('wordprocessingml')) {
    return 'docx'
  }
  if (PRESENTATION_EXTENSIONS.has(ext) || PRESENTATION_MIME_TYPES.has(mimeType) || mimeType.includes('presentationml')) {
    return 'pptx'
  }
  if (IMAGE_EXTENSIONS.has(ext) || mimeType.startsWith('image/')) return 'image'
  if (ext === '.pdf' || mimeType === 'application/pdf') return 'pdf'
  if (ext === '.html' || ext === '.htm' || mimeType === 'text/html') return 'html'
  if (ext === '.mmd' || ext === '.mermaid') return 'mermaid'
  if (ext === '.md' || mimeType === 'text/markdown') return 'markdown'
  if (CODE_EXTENSIONS.has(ext)) return 'code'
  if (STRUCTURED_EXTENSIONS.has(ext) || STRUCTURED_MIME_TYPES.has(mimeType)) return 'structured'
  if (TEXT_EXTENSIONS.has(ext) || mimeType.startsWith('text/')) return 'text'
  return 'binary'
}

function hasSignature(bytes, signature) {
  if (bytes.length < signature.length) return false
  return signature.every((value, index) => bytes[index] === value)
}

function isZipContainer(buffer) {
  const bytes = new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 4))
  return ZIP_SIGNATURES.some((signature) => hasSignature(bytes, signature))
}

function isOleContainer(buffer) {
  const bytes = new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 8))
  return hasSignature(bytes, OLE_SIGNATURE)
}

function describeHeader(buffer) {
  const bytes = new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 16))
  const ascii = Array.from(bytes, (value) => (value >= 32 && value <= 126 ? String.fromCharCode(value) : '.')).join('')
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join(' ')
  return `"${ascii}" (${hex})`
}

function assertValidOfficeBuffer(file, ext, previewKind, buffer) {
  if (previewKind === 'spreadsheet') {
    if (ext === '.xlsx') {
      if (!isZipContainer(buffer)) {
        throw new Error(`Invalid Excel file: .xlsx files must start with a ZIP header. Actual header: ${describeHeader(buffer)}`)
      }
      return
    }

    if (ext === '.xls') {
      if (!isZipContainer(buffer) && !isOleContainer(buffer)) {
        throw new Error(`Invalid Excel file: .xls files must use an OLE or ZIP Office container. Actual header: ${describeHeader(buffer)}`)
      }
      return
    }
  }

  if (previewKind === 'docx') {
    if (ext === '.docx' && !isZipContainer(buffer)) {
      throw new Error(`Invalid Word file: .docx files must start with a ZIP header. Actual header: ${describeHeader(buffer)}`)
    }
    if (ext === '.doc' && !isZipContainer(buffer) && !isOleContainer(buffer)) {
      throw new Error(`Invalid Word file: .doc files must use an OLE or ZIP Office container. Actual header: ${describeHeader(buffer)}`)
    }
  }

  if (previewKind === 'pptx' && !isZipContainer(buffer)) {
    throw new Error(`Invalid PowerPoint file: .pptx files must start with a ZIP header. Actual header: ${describeHeader(buffer)}`)
  }
}

function sanitizeDocument(doc) {
  const blockedTags = ['script', 'iframe', 'object', 'embed', 'meta', 'base', 'link[rel="import"]']
  blockedTags.forEach((selector) => {
    doc.querySelectorAll(selector).forEach((node) => node.remove())
  })

  doc.querySelectorAll('*').forEach((node) => {
    Array.from(node.attributes).forEach((attr) => {
      const name = attr.name.toLowerCase()
      const value = attr.value.trim().toLowerCase()

      if (name.startsWith('on') || name === 'srcdoc') {
        node.removeAttribute(attr.name)
        return
      }

      if (
        ['href', 'src', 'xlink:href', 'action', 'formaction'].includes(name) &&
        (value.startsWith('javascript:') || value.startsWith('data:text/html'))
      ) {
        node.removeAttribute(attr.name)
        return
      }

      if (name === 'target') {
        node.setAttribute('target', '_blank')
        node.setAttribute('rel', 'noopener noreferrer')
      }
    })
  })
}

function sanitizeHtmlDocument(html) {
  if (typeof DOMParser === 'undefined') return html
  const parser = new DOMParser()
  const doc = parser.parseFromString(html, 'text/html')
  sanitizeDocument(doc)
  return `<!DOCTYPE html>\n${doc.documentElement.outerHTML}`
}

function sanitizeHtmlFragment(html) {
  if (typeof DOMParser === 'undefined') return html
  const parser = new DOMParser()
  const doc = parser.parseFromString(`<body>${html}</body>`, 'text/html')
  sanitizeDocument(doc)
  return doc.body.innerHTML
}

export default function RichFilePreview({
  file,
  cacheKey,
  loadText,
  loadArrayBuffer,
  loadBlob,
  fallbackText = null,
}) {
  const { t } = useTranslation()
  const [content, setContent] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tables, setTables] = useState(null)
  const [pptxBuffer, setPptxBuffer] = useState(null)
  const [activeSheetIndex, setActiveSheetIndex] = useState(0)

  const ext = getFileExtension(file)
  const previewKind = inferPreviewKind(file, ext)
  const key = cacheKey || file.path || file.uuid || file.name || file.original_name || `${ext}:${previewKind}`

  useEffect(() => {
    let cancelled = false
    let objectUrl = null

    setContent(null)
    setTables(null)
    setPptxBuffer(null)
    setActiveSheetIndex(0)
    setError(null)
    setLoading(true)

    const fetchTextContent = async () => {
      if (typeof file.content === 'string') return file.content
      if (typeof loadText === 'function') return loadText()
      if (fallbackText != null) return fallbackText
      throw new Error('No text preview available')
    }

    const fetchArrayBuffer = async () => {
      if (typeof loadArrayBuffer === 'function') return loadArrayBuffer()
      throw new Error('No binary preview available')
    }

    const fetchBlob = async () => {
      if (typeof loadBlob === 'function') return loadBlob()
      throw new Error('No blob preview available')
    }

    const run = async () => {
      try {
        if (previewKind === 'csv') {
          const text = await fetchTextContent()
          const Papa = (await import('papaparse')).default
          const isTsv = ext === '.tsv' || getMimeType(file) === 'text/tab-separated-values'
          const result = Papa.parse(text, {
            skipEmptyLines: true,
            ...(isTsv ? { delimiter: '\t' } : {}),
          })
          if (!cancelled) setTables([{ name: isTsv ? 'TSV' : 'CSV', rows: result.data }])
          return
        }

	        if (previewKind === 'spreadsheet') {
	          const buffer = await fetchArrayBuffer()
	          assertValidOfficeBuffer(file, ext, previewKind, buffer)
	          let allSheets
	          if (ext === '.xlsx') {
	            allSheets = await readXlsxWorkbook(buffer)
	          } else {
	            const XLSX = await import('xlsx')
	            const workbook = XLSX.read(new Uint8Array(buffer), { type: 'array' })
	            allSheets = workbook.SheetNames.map((sheetName) => ({
	              name: sheetName,
	              rows: XLSX.utils.sheet_to_json(workbook.Sheets[sheetName], { header: 1, defval: '' }),
	            }))
	          }
	          const firstNonEmptySheetIndex = allSheets.findIndex((sheet) =>
	            sheet.rows.some((row) => Array.isArray(row) && row.some((cell) => String(cell ?? '').trim() !== ''))
	          )
          if (!cancelled) {
            setTables(allSheets)
            setActiveSheetIndex(firstNonEmptySheetIndex >= 0 ? firstNonEmptySheetIndex : 0)
          }
          return
        }

        if (previewKind === 'docx') {
          const buffer = await fetchArrayBuffer()
          assertValidOfficeBuffer(file, ext, previewKind, buffer)
          const mammoth = await import('mammoth')
          const result = await mammoth.convertToHtml({ arrayBuffer: buffer })
          if (!cancelled) setContent(sanitizeHtmlFragment(result.value))
          return
        }

        if (previewKind === 'pptx') {
          const buffer = await fetchArrayBuffer()
          assertValidOfficeBuffer(file, ext, previewKind, buffer)
          if (!cancelled) setPptxBuffer(buffer)
          return
        }

        if (previewKind === 'pdf') {
          const blob = await fetchBlob()
          objectUrl = URL.createObjectURL(blob)
          if (!cancelled) setContent(objectUrl)
          return
        }

        if (previewKind === 'image') {
          const blob = await fetchBlob()
          objectUrl = URL.createObjectURL(blob)
          if (!cancelled) setContent(objectUrl)
          return
        }

        if (previewKind === 'html') {
          const text = await fetchTextContent()
          if (!cancelled) setContent(sanitizeHtmlDocument(text))
          return
        }

        if (previewKind === 'markdown' || previewKind === 'mermaid' || previewKind === 'code' || previewKind === 'structured' || previewKind === 'text') {
          const text = await fetchTextContent()
          if (!cancelled) setContent(text)
          return
        }
      } catch (err) {
        if (!cancelled) {
          if (
            fallbackText != null &&
            (previewKind === 'markdown' || previewKind === 'mermaid' || previewKind === 'code' || previewKind === 'structured' || previewKind === 'text' || previewKind === 'html')
          ) {
            setContent(previewKind === 'html' ? sanitizeHtmlDocument(fallbackText) : fallbackText)
            setError(null)
          } else {
            setError(err?.message || String(err))
          }
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    run()

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [cacheKey, ext, fallbackText, file, key, loadArrayBuffer, loadBlob, loadText, previewKind])

  if (loading) {
    return (
      <div className="flex flex-col gap-2 p-4">
        <div className="skeleton" style={{ width: '100%', height: 16 }} />
        <div className="skeleton" style={{ width: '80%', height: 16 }} />
        <div className="skeleton" style={{ width: '60%', height: 16 }} />
        <div className="skeleton" style={{ width: '90%', height: 16 }} />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-4 text-xs" style={{ color: 'var(--red)' }}>
        {t('userData.previewError')}: {error}
      </div>
    )
  }

  if (tables) {
    if (previewKind === 'spreadsheet') {
      return (
        <WorkbookPreview
          sheets={tables}
          activeSheetIndex={activeSheetIndex}
          onSelectSheet={setActiveSheetIndex}
          t={t}
          filePath={file.path || file.name || file.original_name || ''}
        />
      )
    }
    return <TablePreview rows={tables[0]?.rows || []} />
  }

  if (previewKind === 'docx' && content) {
    return (
      <div
        className="px-4 py-3"
        style={{ color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.6, wordBreak: 'break-word' }}
        dangerouslySetInnerHTML={{ __html: content }}
      />
    )
  }

  if (previewKind === 'pptx' && pptxBuffer) {
    return <PptxPreview buffer={pptxBuffer} />
  }

  if (previewKind === 'pdf' && content) {
    return (
      <object
        data={content}
        type="application/pdf"
        style={{ width: '100%', height: '100%', minHeight: 500, border: 'none' }}
      >
        <div className="p-4 text-xs" style={{ color: 'var(--text-dim)' }}>
          {t('userData.pdfNotSupported')}
        </div>
      </object>
    )
  }

  if (previewKind === 'image' && content) {
    return <ImagePreview src={content} alt={file.name || file.original_name || 'image preview'} />
  }

  if (previewKind === 'html' && content) {
    return <HtmlPreview content={content} />
  }

  if (previewKind === 'markdown' && content) {
    return <MarkdownPreview content={content} />
  }

  if (previewKind === 'mermaid' && content != null) {
    return <MermaidFilePreview content={content} />
  }

  if ((previewKind === 'code' || previewKind === 'structured') && content) {
    return <CodePreview content={content} language={getLanguage(ext)} />
  }

  if (content != null) {
    return <TextPreview content={content} />
  }

  return (
    <div className="p-4 text-xs" style={{ color: 'var(--text-dim)' }}>
      {t('userData.noPreview')}
    </div>
  )
}
