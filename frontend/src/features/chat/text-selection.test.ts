import { describe, expect, it } from 'vitest'

import { locateSelectionInContent } from './text-selection'

describe('locateSelectionInContent', () => {
  it('uses prefix and suffix hints to distinguish duplicate selected text', () => {
    const content = 'First alpha appears here. Later alpha appears there.'

    expect(
      locateSelectionInContent(content, 'alpha', {
        occurrenceIndex: 0,
        prefixHint: 'Later ',
        suffixHint: ' appears there.',
      }),
    ).toEqual({
      selected_text: 'alpha',
      prefix: 'First alpha appears here. Later ',
      suffix: ' appears there.',
      contentMatched: true,
    })
  })

  it('recovers a durable source span when rendered whitespace differs', () => {
    expect(
      locateSelectionInContent('One\n\n  two\tthree', 'One two three'),
    ).toEqual({
      selected_text: 'One\n\n  two\tthree',
      prefix: '',
      suffix: '',
      contentMatched: true,
    })
  })

  it('returns a capped, non-matched payload when no durable span can be found', () => {
    const selectedText = `Rendered-only ${'text '.repeat(200)}`

    expect(locateSelectionInContent('Different source', selectedText)).toEqual({
      selected_text: selectedText.trim().slice(0, 500),
      prefix: '',
      suffix: '',
      contentMatched: false,
    })
  })
})
