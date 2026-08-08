import { memo, useMemo } from 'react'
import { Streamdown, defaultUrlTransform } from 'streamdown'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github-dark.css'
import { createMarkdownComponents } from './markdownComponents'

const REHYPE_PLUGINS = [rehypeHighlight]
const LINK_SAFETY = { enabled: false }

function normalizeLeadingMetadataBreaks(content) {
  const match = content.match(/^(---\r?\n)([\s\S]*?)(\r?\n---)(?=\r?\n|$)/)
  if (!match) return content

  const [, start, body, end] = match
  const normalizedBody = body
    .split(/\r?\n/)
    .map((line) => `${line}  `)
    .join('\n')

  return `${start}${normalizedBody}${end}${content.slice(match[0].length)}`
}

function MarkdownRenderer({
  content,
  mermaidCollapsible = false,
  streaming = false,
  streamed = false,
}) {
  const components = useMemo(
    () => createMarkdownComponents({ mermaidCollapsible }),
    [mermaidCollapsible]
  )
  if (!content) return null

  const normalizedContent = normalizeLeadingMetadataBreaks(content)

  return (
    <div className="markdown-body overflow-hidden" style={{ wordBreak: 'break-word' }}>
      <Streamdown
        // Keep a once-streamed block on Streamdown's block-based tree after
        // completion so code/diagram nodes are not remounted at hand-off.
        mode={streaming || streamed ? 'streaming' : 'static'}
        parseIncompleteMarkdown
        // Streamdown uses this flag to mark the trailing code fence incomplete;
        // token animation itself remains disabled below.
        isAnimating={streaming}
        animated={false}
        controls={false}
        lineNumbers={false}
        linkSafety={LINK_SAFETY}
        urlTransform={defaultUrlTransform}
        rehypePlugins={REHYPE_PLUGINS}
        components={components}
      >
        {normalizedContent}
      </Streamdown>
    </div>
  )
}

export default memo(MarkdownRenderer)
