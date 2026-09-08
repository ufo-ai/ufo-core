/** A browser closes a window a script opened, and only that one, so the provider's return page can take
 *  itself away. The window opens on the press: minting first would lose the gesture the browser demands. */

const CONSENT_WINDOW = "ufo-connect";
const CONSENT_WIDTH = 520;
const CONSENT_HEIGHT = 720;

/** A window `window.open` makes starts with a copy of this tab's session storage; a tab opened from a
 *  link carries none. `core/src/ufo/sdk/callback_page.py` spells the same key, and a gate holds them equal. */
const CONSENT_WINDOW_MARK = "ufo-consent-window";

function consentFeatures(): string {
  const left = window.screenX + Math.max(0, (window.outerWidth - CONSENT_WIDTH) / 2);
  const top = window.screenY + Math.max(0, (window.outerHeight - CONSENT_HEIGHT) / 3);
  return [
    "popup",
    `width=${CONSENT_WIDTH}`,
    `height=${CONSENT_HEIGHT}`,
    `left=${Math.round(left)}`,
    `top=${Math.round(top)}`,
  ].join(",");
}

export function openConsentWindow(url = ""): Window | null {
  /* Marked before the open, because the copy the new window starts with is taken then. A browser that
     refuses storage leaves it unwritten, and the return page carries that window to the connectors screen. */
  try {
    sessionStorage.setItem(CONSENT_WINDOW_MARK, "1");
  } catch {
  }
  const consent = window.open(url, CONSENT_WINDOW, consentFeatures());
  consent?.focus();
  return consent;
}

export function ConsentLink({
  url,
  className,
  children,
}: {
  url: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener"
      className={className}
      onClick={(event) => {
        if (openConsentWindow(url)) event.preventDefault();
      }}
    >
      {children}
    </a>
  );
}
