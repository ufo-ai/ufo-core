/** A stored preview, inlined so the examples draw what the app draws: the portal serves every
 *  picture on a turn from its own origin, so no docs page may name a picture host. */
const picture = (body: string): string =>
  "data:image/svg+xml;charset=utf-8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">' +
      body +
      "</svg>",
  );

export const SHOT_REVENUE = picture(
  '<rect width="320" height="180" fill="#edeae4"/>' +
    '<rect x="28" y="108" width="34" height="48" fill="#6b7280"/>' +
    '<rect x="78" y="86" width="34" height="70" fill="#6b7280"/>' +
    '<rect x="128" y="94" width="34" height="62" fill="#6b7280"/>' +
    '<rect x="178" y="58" width="34" height="98" fill="#39414d"/>' +
    '<rect x="228" y="70" width="34" height="86" fill="#6b7280"/>' +
    '<rect x="28" y="156" width="264" height="2" fill="#b9b4aa"/>',
);

export const SHOT_TREND = picture(
  '<rect width="320" height="180" fill="#e6e9ec"/>' +
    '<polyline points="28,134 80,112 132,122 184,74 236,92 292,46" fill="none" stroke="#39414d" stroke-width="4"/>' +
    '<circle cx="292" cy="46" r="6" fill="#39414d"/>' +
    '<rect x="28" y="156" width="264" height="2" fill="#b4bcc4"/>',
);

export const SHOT_SIDEBAR = picture(
  '<rect width="320" height="180" fill="#f2f0ec"/>' +
    '<rect x="0" y="0" width="86" height="180" fill="#dedad2"/>' +
    '<rect x="16" y="24" width="54" height="8" fill="#a9a399"/>' +
    '<rect x="16" y="48" width="42" height="8" fill="#c0bab0"/>' +
    '<rect x="16" y="68" width="48" height="8" fill="#c0bab0"/>' +
    '<rect x="110" y="24" width="180" height="10" fill="#8d8880"/>' +
    '<rect x="110" y="56" width="164" height="8" fill="#c9c3b9"/>' +
    '<rect x="110" y="76" width="140" height="8" fill="#c9c3b9"/>' +
    '<rect x="110" y="120" width="120" height="28" fill="#39414d"/>',
);

export const SHOT_PAGE = picture(
  '<rect width="320" height="180" fill="#ffffff"/>' +
    '<rect x="32" y="28" width="150" height="12" fill="#39414d"/>' +
    '<rect x="32" y="60" width="256" height="8" fill="#cfcac2"/>' +
    '<rect x="32" y="80" width="256" height="8" fill="#cfcac2"/>' +
    '<rect x="32" y="100" width="212" height="8" fill="#cfcac2"/>' +
    '<rect x="32" y="132" width="256" height="8" fill="#cfcac2"/>' +
    '<rect x="32" y="152" width="178" height="8" fill="#cfcac2"/>',
);
