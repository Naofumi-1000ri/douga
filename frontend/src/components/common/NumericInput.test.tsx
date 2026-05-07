import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import NumericInput from './NumericInput'

describe('NumericInput', () => {
  let onCommit: (value: number) => void

  beforeEach(() => {
    onCommit = vi.fn<(value: number) => void>()
  })

  it('Enter で commit が 1 回だけ呼ばれる（二重発火防止）', async () => {
    const user = userEvent.setup()
    render(<NumericInput value={10} onCommit={onCommit} data-testid="input" />)
    const input = screen.getByTestId('input') as HTMLInputElement

    await user.click(input)
    await user.clear(input)
    await user.type(input, '42')
    await user.keyboard('{Enter}')

    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith(42)
  })

  it('Tab (blur) で commit が 1 回呼ばれる', async () => {
    const user = userEvent.setup()
    render(<NumericInput value={10} onCommit={onCommit} data-testid="input" />)
    const input = screen.getByTestId('input') as HTMLInputElement

    await user.click(input)
    await user.clear(input)
    await user.type(input, '99')
    await user.tab()

    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith(99)
  })

  it('Escape で commit が呼ばれず、表示が元の value に戻る', async () => {
    const user = userEvent.setup()
    render(<NumericInput value={10} onCommit={onCommit} data-testid="input" />)
    const input = screen.getByTestId('input') as HTMLInputElement

    await user.click(input)
    await user.clear(input)
    await user.type(input, '55')
    await user.keyboard('{Escape}')

    expect(onCommit).toHaveBeenCalledTimes(0)
    expect(input.value).toBe('10')
  })

  it('フォーカス中は外部 value 変更で表示が上書きされない', async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <NumericInput value={10} onCommit={onCommit} data-testid="input" />,
    )
    const input = screen.getByTestId('input') as HTMLInputElement

    await user.click(input)
    await user.clear(input)
    await user.type(input, '77')

    // フォーカス中に親コンポーネントが value を変更
    rerender(<NumericInput value={999} onCommit={onCommit} data-testid="input" />)

    // 編集中の文字列が上書きされないこと
    expect(input.value).toBe('77')
  })

  it('空欄で確定（blur）すると onCommit が呼ばれず、入力が元の value に戻る', async () => {
    const user = userEvent.setup()
    render(<NumericInput value={10} onCommit={onCommit} data-testid="input" />)
    const input = screen.getByTestId('input') as HTMLInputElement

    await user.click(input)
    await user.clear(input)
    await user.tab()

    expect(onCommit).toHaveBeenCalledTimes(0)
    expect(input.value).toBe('10')
  })
})
