import type { InputHTMLAttributes } from "react";

/** A text input. Labels live beside the input — placeholders are not
 *  labels, they vanish on the first character and the field is then
 *  unlabelled. Use the visible `<label>` from the form that hosts this,
 *  or pass `aria-label` when a visible label is not the right shape.
 *
 *  Style lives in `index.css` under `input[type="text"]`. */
export function Input({
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...rest}
      type={rest.type ?? "text"}
      className={className}
    />
  );
}
