import { createContext, useContext } from 'react'

const MarkdownRenderContext = createContext({
  resolveInlineFiles: false,
  inlineFileProbeDeferred: false,
  filePreviewCwd: '',
})

export function useMarkdownRenderContext() {
  return useContext(MarkdownRenderContext)
}

export default MarkdownRenderContext
