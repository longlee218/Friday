/** Re-export of `navigate` for components outside the App tree.
 *
 *  App owns the path state via `useRoute`; components that need to
 *  navigate (a card click on the Monitor screen, for example) call
 *  `navigate` and rely on the App tree re-rendering on the
 *  `popstate` event we dispatch. */
export { navigate } from "./App";
