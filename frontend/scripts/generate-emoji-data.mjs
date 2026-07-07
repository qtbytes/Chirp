// Generates src/emojiData.ts from @emoji-mart/data (native set).
// Run with: npm run generate:emoji
//
// @emoji-mart/data is a devDependency used only by this script; the generated
// file is fully self-contained, so nothing is imported from it at runtime.

import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { writeFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const data = require("@emoji-mart/data/sets/15/native.json");

const __dirname = dirname(fileURLToPath(import.meta.url));

// Map @emoji-mart category ids into the app's picker categories (in order).
const CATEGORY_DEFS = [
  { emojiMartId: "people", id: "smileys", label: "Smileys & people", icon: "\u{1F600}" },
  { emojiMartId: "nature", id: "animals", label: "Animals & nature", icon: "\u{1F43B}" },
  { emojiMartId: "foods", id: "food", label: "Food & drink", icon: "\u{1F354}" },
  { emojiMartId: "activity", id: "activities", label: "Activities", icon: "⚽" },
  { emojiMartId: "places", id: "travel", label: "Travel & places", icon: "\u{1F697}" },
  { emojiMartId: "objects", id: "objects", label: "Objects", icon: "\u{1F4A1}" },
  { emojiMartId: "symbols", id: "symbols", label: "Symbols", icon: "❤️" },
  { emojiMartId: "flags", id: "flags", label: "Flags", icon: "\u{1F6A9}" },
];

const categoryById = new Map(data.categories.map((category) => [category.id, category]));

const categories = CATEGORY_DEFS.map((def) => {
  const source = categoryById.get(def.emojiMartId);
  const emojis = [];
  const seen = new Set();
  for (const emojiId of source?.emojis || []) {
    const entry = data.emojis[emojiId];
    const char = entry?.skins?.[0]?.native;
    if (!char || seen.has(char)) continue;
    seen.add(char);
    emojis.push({
      char,
      name: entry.name.toLowerCase(),
      keywords: (entry.keywords || []).join(" ").toLowerCase(),
    });
  }
  return { id: def.id, label: def.label, icon: def.icon, emojis };
});

const total = categories.reduce((count, category) => count + category.emojis.length, 0);

const header = `// AUTO-GENERATED from @emoji-mart/data (sets/15/native.json).
// Do not edit by hand. To regenerate, run: npm run generate:emoji

export interface EmojiItem {
  char: string;
  name: string;
  keywords: string;
}

export interface EmojiCategory {
  id: string;
  label: string;
  icon: string;
  emojis: EmojiItem[];
}

export const EMOJI_CATEGORIES: EmojiCategory[] = `;

const outPath = resolve(__dirname, "..", "src", "emojiData.ts");
writeFileSync(outPath, header + JSON.stringify(categories, null, 2) + ";\n");
console.log(`Wrote ${outPath} with ${total} emojis across ${categories.length} categories.`);
