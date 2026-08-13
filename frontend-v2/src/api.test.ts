import { describe, expect, it } from 'vitest'
import { flattenStructuredFields } from './api'

describe('flattenStructuredFields', () => {
  it('does not silently drop structured extraction fields after 250 entries', () => {
    const structured = Object.fromEntries(
      Array.from({ length: 300 }, (_, index) => [`field_${index}`, `value_${index}`]),
    )

    const fields = flattenStructuredFields(structured)

    expect(fields).toHaveLength(300)
    expect(fields.at(-1)).toMatchObject({ key: 'field_299', value: 'value_299' })
  })
})
