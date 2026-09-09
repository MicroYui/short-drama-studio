import { useEffect, useRef, useState } from 'react'

interface Props {
  id: string
  label: string
  ariaLabel?: string
  value: number
  min: number
  max?: number
  readOnly?: boolean
  onChange: (seconds: number) => void
  onValidityChange: (id: string, valid: boolean) => void
}

const secondsText = (value: number) => String(Number(value.toFixed(6)))

/** Keep unfinished input local while only valid times enter the shot draft. */
export function ShotSecondsInput({ id, label, ariaLabel, value, min, max, readOnly, onChange, onValidityChange }: Props) {
  const [text, setText] = useState(() => secondsText(value))
  const [error, setError] = useState('')
  const accepted = useRef(value)

  useEffect(() => {
    if (accepted.current === value) return
    accepted.current = value
    setText(secondsText(value))
    setError('')
    onValidityChange(id, true)
  }, [id, value, onValidityChange])
  useEffect(() => () => onValidityChange(id, true), [id, onValidityChange])

  const change = (next: string) => {
    setText(next)
    const seconds = Number(next)
    const message = !next.trim() || !Number.isFinite(seconds) ? '请输入秒数'
      : seconds < min ? `最少 ${secondsText(min)} 秒`
      : max !== undefined && seconds > max ? `最多 ${secondsText(max)} 秒` : ''
    setError(message)
    onValidityChange(id, !message)
    if (!message) {
      accepted.current = seconds
      onChange(seconds)
    }
  }

  return <label className="sd-seconds-field" htmlFor={id}>
    <span>{label}</span>
    <input id={id} type="number" inputMode="decimal" step="any" min={min} max={max}
      value={text} readOnly={readOnly} aria-label={ariaLabel ?? label}
      aria-invalid={!!error} aria-describedby={error ? `${id}-error` : undefined}
      onChange={event => change(event.target.value)} />
    {error && <small id={`${id}-error`} role="alert">{error}</small>}
  </label>
}
