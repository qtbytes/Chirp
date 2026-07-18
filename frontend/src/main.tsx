import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { polyfillCountryFlagEmojis } from "country-flag-emoji-polyfill";
// Vendored from country-flag-emoji-polyfill/dist (MIT; flag art Twemoji,
// CC-BY 4.0): the package's exports map hides the file, and its default is a
// jsdelivr CDN URL — this app self-hosts, so the font must ship with it.
import flagFontUrl from "./assets/TwemojiCountryFlags.woff2";
import App from "./App";
import "./styles.css";

// Windows ships no country-flag glyphs (Segoe UI Emoji leaves the regional
// indicators as "CN"/"DE" letters), so this detects that and injects a small
// woff2 containing only the flag sequences. The font is first in the app's
// font-family stack but defines nothing except flags, so on macOS/Android —
// where detection says flags render natively — nothing is even downloaded.
polyfillCountryFlagEmojis("Twemoji Country Flags", flagFontUrl);
// The browser only fetches an @font-face when a glyph first needs it, which
// made the first flag render as letters for a beat. Warm it at startup —
// resolves instantly (no download) on platforms where the polyfill declined.
void document.fonts.load('1em "Twemoji Country Flags"', "🇺🇳");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
