import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Markdown } from './Markdown'

describe('Markdown', () => {
  it('renders bold text', () => {
    const html = renderToStaticMarkup(<Markdown>{'**bold**'}</Markdown>)
    expect(html).toContain('<strong>bold</strong>')
  })

  it('renders GFM tables', () => {
    const html = renderToStaticMarkup(<Markdown>{'| a | b |\n| - | - |\n| 1 | 2 |'}</Markdown>)
    expect(html).toContain('<table>')
    expect(html).toContain('<td>1</td>')
  })
})
