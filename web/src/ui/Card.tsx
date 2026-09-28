import type { HTMLAttributes, ReactNode } from "react";

/** A card. The header / body split is convention: header carries the
 *  identifying bits (name, age, type), body the content. Style lives in
 *  `index.css` under `.card` and `.card > header`.
 *
 *  Anything fancier than title + body is a screen, and screens are not
 *  primitives — composition lives above this layer. */
export function Card({
  title,
  children,
  className,
  ...rest
}: HTMLAttributes<HTMLElement> & {
  title?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      {...rest}
      className={["card", className].filter(Boolean).join(" ")}
    >
      {title && <header>{title}</header>}
      {children}
    </section>
  );
}
