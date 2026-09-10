const LAYER = '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]';

/** A dialog or a menu dismisses the moment focus leaves it, so a surface taking focus on its own —
 *  a lane arriving, a composer standing up — must not reach across an open one. */
export function takeFocus(node: HTMLElement | null, options?: FocusOptions): void {
  const active = document.activeElement;
  if (node === null || (active instanceof Element && active.closest(LAYER) !== null)) return;
  node.focus(options);
}
