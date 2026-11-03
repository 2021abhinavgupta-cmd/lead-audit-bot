// Input helpers shared by the Social tab and the Full Audit page. Pure, no React.

// "name", "@name" or an instagram.com profile link -> "name", else null.
export function parseInstagramUsername(text) {
  const raw = String(text || '').trim()
    .replace(/^(https?:\/\/)?(www\.)?instagram\.com\//i, '')
    .replace(/^@/, '')
    .split(/[/?#]/)[0];
  return /^[A-Za-z0-9._]{1,30}$/.test(raw) ? raw : null;
}

const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;

// A website link typed by hand -> { url, host } with https:// added when no
// scheme was given, or null when it does not look like a public site. Only a
// first filter for typos: the server still runs validate_public_url.
export function normaliseWebsite(text) {
  let url = String(text || '').trim();
  if (!url) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) url = `https://${url}`;
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
  if (parsed.username || parsed.password) return null;
  const host = parsed.hostname.toLowerCase().replace(/\.$/, '');
  if (!host || IPV4.test(host) || host.includes(':') || host.startsWith('[')) return null;
  const labels = host.split('.');
  if (labels.length < 2 || labels.some(l => !l)) return null;
  if (!/^[a-z]{2,}$|^xn--[a-z0-9-]+$/.test(labels[labels.length - 1])) return null;
  if (['localhost', 'local', 'internal', 'lan', 'home', 'test', 'invalid', 'example'].includes(labels[labels.length - 1])) return null;
  return { url, host: host.replace(/^www\./, '') };
}

// Company name for the Full Audit page: what was typed, else the Instagram
// display name once known, else the website host, else the username.
export function defaultCompany({ typed, displayName, host, username }) {
  return (typed || '').trim() || (displayName || '').trim() || host || username || '';
}
