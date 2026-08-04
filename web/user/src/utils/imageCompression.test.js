import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isSupportedImageFile,
  requiresImageRasterization,
  resolveImageMediaType,
} from './imageCompression.js'


test('recognizes SVG and BMP from MIME type or extension', () => {
  assert.equal(resolveImageMediaType({ name: 'diagram.svg', type: 'image/svg+xml' }), 'image/svg+xml')
  assert.equal(resolveImageMediaType({ name: 'scan.bmp', type: '' }), 'image/bmp')
  assert.equal(resolveImageMediaType({ name: 'scan.bin', type: 'image/x-ms-bmp' }), 'image/bmp')
})


test('marks SVG and BMP for PNG rasterization', () => {
  assert.equal(requiresImageRasterization({ name: 'diagram.svg', type: 'image/svg+xml' }), true)
  assert.equal(requiresImageRasterization({ name: 'scan.bmp', type: 'image/bmp' }), true)
  assert.equal(requiresImageRasterization({ name: 'photo.jpg', type: 'image/jpeg' }), false)
})


test('does not advertise arbitrary image MIME types as supported', () => {
  assert.equal(isSupportedImageFile({ name: 'scan.tiff', type: 'image/tiff' }), false)
  assert.equal(resolveImageMediaType({ name: 'scan.tiff', type: 'image/tiff' }), null)
})
