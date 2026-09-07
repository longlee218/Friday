import type { ButtonHTMLAttributes, ReactNode } from "react";

/** A button. Pass `primary` for the single action on a screen, leave it
 *  otherwise for the secondary ones.
 *
 *  Style lives in `index.css` under `button` and `button.primary`.
 *  Hover and disabled states inherit from those. */
export function Button({
  primary,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  primary?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      {...rest}
      className={primary ? "primary" : undefined}
    >
      {children}
    </button>
  );
}
