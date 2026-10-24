import MarkdownIt from 'markdown-it'

// Only Markdown-generated markup reaches the view. Model HTML and automatic
// image requests remain disabled for both partial and completed answers.
const markdown = new MarkdownIt({ html: false, linkify: false, typographer: false, breaks: true, maxNesting: 32 })
markdown.disable('image')
markdown.validateLink = address => {
  if (/[\u0000-\u0020\u007f]/.test(address)) return false
  try { return ['http:', 'https:'].includes(new URL(address).protocol) } catch { return false }
}
markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return renderer.renderToken(tokens, index, options)
}

export function renderPartnerMarkdown(value) {
  return markdown.render(typeof value === 'string' ? value : '')
}
