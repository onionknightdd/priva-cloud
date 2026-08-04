const MAX_DIMENSION = 2048
const QUALITY_LADDER = [0.8, 0.65, 0.5, 0.35]
const MAX_SCALE_STEPS = 2
const DEFAULT_MAX_BYTES = 5 * 1024 * 1024

const INPUT_IMAGE_MEDIA_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'image/svg+xml',
  'image/bmp',
])

const MEDIA_TYPE_ALIASES = new Map([
  ['image/jpg', 'image/jpeg'],
  ['image/svg', 'image/svg+xml'],
  ['image/x-bmp', 'image/bmp'],
  ['image/x-ms-bmp', 'image/bmp'],
  ['image/x-windows-bmp', 'image/bmp'],
])

const EXTENSION_MEDIA_TYPES = new Map([
  ['.png', 'image/png'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.gif', 'image/gif'],
  ['.webp', 'image/webp'],
  ['.svg', 'image/svg+xml'],
  ['.bmp', 'image/bmp'],
])

const RASTERIZE_TO_PNG_TYPES = new Set(['image/svg+xml', 'image/bmp'])

function normalizedMediaType(raw) {
  const value = String(raw || '').trim().toLowerCase()
  return MEDIA_TYPE_ALIASES.get(value) || value
}

export function resolveImageMediaType(file) {
  const declared = normalizedMediaType(file?.type)
  if (INPUT_IMAGE_MEDIA_TYPES.has(declared)) return declared

  const name = String(file?.name || '').toLowerCase()
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? (EXTENSION_MEDIA_TYPES.get(name.slice(dot)) || null) : null
}

export function isSupportedImageFile(file) {
  return resolveImageMediaType(file) !== null
}

export function requiresImageRasterization(file) {
  return RASTERIZE_TO_PNG_TYPES.has(resolveImageMediaType(file))
}

/**
 * Process an image file: read as base64, compress if over maxBytes.
 * @param {File} file
 * @param {number} maxBytes - max decoded size in bytes (default 5 MiB)
 * @returns {Promise<{base64: string, mediaType: string, finalSize: number}>}
 */
export async function processImage(file, maxBytes = DEFAULT_MAX_BYTES) {
  const mediaType = resolveImageMediaType(file)
  if (!mediaType) throw new Error('Unsupported image type')

  // Claude accepts PNG/JPEG/GIF/WebP image blocks. SVG and BMP are supported
  // by rasterizing them to PNG in-browser before the multimodal request.
  const mustRasterize = RASTERIZE_TO_PNG_TYPES.has(mediaType)

  // Fast path: small enough already — no pixel decode at all.
  if (!mustRasterize && file.size <= maxBytes) {
    const base64 = await blobToBase64(file)
    return { base64, mediaType, finalSize: file.size }
  }

  return compressImage(file, maxBytes, mediaType, mustRasterize ? 'image/png' : null)
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = () => reject(reader.error || new Error('Failed to read file'))
    reader.readAsDataURL(blob)
  })
}

function canvasToBlob(canvas, type, quality) {
  return new Promise((resolve) => {
    canvas.toBlob(resolve, type, quality)
  })
}

function yieldToMain() {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

// Decode the file straight into a bitmap, downscaled at decode time when the
// platform supports resize options — this avoids ever materializing the full
// pixel buffer for very large photos.
async function decodeImage(file, resizeWidth, resizeHeight) {
  if (typeof createImageBitmap === 'function') {
    if (resizeWidth && resizeHeight) {
      try {
        return await createImageBitmap(file, { resizeWidth, resizeHeight, resizeQuality: 'high' })
      } catch {
        // Older engines reject the options bag — fall through to plain decode.
      }
    }
    try {
      return await createImageBitmap(file)
    } catch {
      // Some formats are unsupported by createImageBitmap — fall through.
    }
  }

  const url = URL.createObjectURL(file)
  try {
    return await new Promise((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => reject(new Error('Failed to decode image'))
      img.src = url
    })
  } finally {
    URL.revokeObjectURL(url)
  }
}

// Probe natural dimensions cheaply so we can ask createImageBitmap to
// downscale during decode.
async function probeDimensions(file) {
  if (typeof createImageBitmap === 'function') {
    try {
      const bmp = await createImageBitmap(file)
      const dims = { width: bmp.width, height: bmp.height, bitmap: bmp }
      return dims
    } catch { /* fall through */ }
  }
  return null
}

async function compressImage(file, maxBytes, mimeType, forcedOutputType) {
  // Preserve SVG/BMP rasterization as PNG; oversized native PNG becomes JPEG.
  // Canvas cannot encode animated GIF, so an oversized GIF becomes a PNG first frame.
  const outputType = forcedOutputType
    || (mimeType === 'image/png' ? 'image/jpeg' : mimeType === 'image/gif' ? 'image/png' : mimeType)

  let source = null
  const canvas = document.createElement('canvas')

  try {
    const probe = await probeDimensions(file)
    let srcWidth
    let srcHeight

    if (probe) {
      srcWidth = probe.width
      srcHeight = probe.height
      const downscale = Math.min(1, MAX_DIMENSION / Math.max(srcWidth, srcHeight))
      if (downscale < 1) {
        const targetW = Math.max(1, Math.round(srcWidth * downscale))
        const targetH = Math.max(1, Math.round(srcHeight * downscale))
        probe.bitmap.close?.()
        source = await decodeImage(file, targetW, targetH)
      } else {
        source = probe.bitmap
      }
    } else {
      source = await decodeImage(file)
    }

    let width = source.width
    let height = source.height
    // Downscale FIRST: cap the longest side before any encode attempt.
    const cap = Math.min(1, MAX_DIMENSION / Math.max(width, height))
    width = Math.max(1, Math.round(width * cap))
    height = Math.max(1, Math.round(height * cap))

    let scale = 1
    let scaleSteps = 0

    for (let attempt = 0; attempt < QUALITY_LADDER.length + MAX_SCALE_STEPS; attempt++) {
      const quality = QUALITY_LADDER[Math.min(attempt, QUALITY_LADDER.length - 1)]
      if (attempt >= QUALITY_LADDER.length) {
        scale *= 0.7
        scaleSteps++
        if (scaleSteps > MAX_SCALE_STEPS) break
      }

      canvas.width = Math.max(1, Math.round(width * scale))
      canvas.height = Math.max(1, Math.round(height * scale))
      const ctx = canvas.getContext('2d')
      ctx.drawImage(source, 0, 0, canvas.width, canvas.height)

      let blob = await canvasToBlob(canvas, outputType, quality)
      if (!blob) {
        // toBlob can return null on some engines — fall back to toDataURL at
        // the already-capped size (bounded memory).
        const dataUrl = canvas.toDataURL(outputType, quality)
        const b64 = dataUrl.split(',')[1]
        const size = Math.floor(b64.length * 3 / 4)
        if (size <= maxBytes) {
          return { base64: b64, mediaType: outputType, finalSize: size }
        }
        await yieldToMain()
        continue
      }

      if (blob.size <= maxBytes) {
        const b64 = await blobToBase64(blob)
        return { base64: b64, mediaType: outputType, finalSize: blob.size }
      }

      blob = null
      // Yield between attempts so multiple concurrent uploads can't starve
      // the main thread.
      await yieldToMain()
    }

    throw new Error('Could not compress image to fit size limit')
  } finally {
    source?.close?.()
    canvas.width = 0
    canvas.height = 0
  }
}
