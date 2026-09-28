import type { ButtonHTMLAttributes, ReactNode } from "react";

/** A square button that carries an icon. The label is required: an icon
 *  alone is invisible to a screen reader and meaningless to a person who
 *  does not recognise it, and a `title` attribute is not a substitute —
 *  it shows on hover, which a touchscreen never has. */
export function IconButton({
  label,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  children: ReactNode;
}) {
  return (
    <button
      {...rest}
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}
