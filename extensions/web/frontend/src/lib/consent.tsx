/** The provider's own consent or install page, opened in a window this page owns.
 *
 *  It is sized for a consent screen and centred on the member's screen. That ownership is what the
 *  page is after: a browser closes a window a script opened, and only that one, so the provider's
 *  return page takes itself away and the member is left looking at the screen they never navigated
 *  off — which has already moved on underneath them, because the turn resumed on the server, or the
 *  step behind the act is watching for the install to land.
 *
 *  Every provider handoff the portal renders goes through here — the consent link in a reply, the
 *  act a first-run step presses, the connector's own consent page — because the window is worth the
 *  same in all three and a member should not have to notice which screen they started from. A
 *  browser that blocks the window gives back nothing, and the caller falls back to a link the member
 *  follows themselves.
 *
 *  `openConsentWindow` takes its URL afterwards for the act that has to mint one first: minting is a
 *  round trip, and a window opened after one has lost the gesture the browser demands to open it at
 *  all. So the act opens the window on the press and points it at the provider when the link lands —
 *  one press, with nothing left on the page for the member to find and press again.
 */

const CONSENT_WINDOW = "ufo-connect";
const CONSENT_WIDTH = 520;
const CONSENT_HEIGHT = 720;

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
