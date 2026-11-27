// Stable, name-based avatar tone selection. Actual colors live in CSS via
// `[data-avatar-tone]` so dark mode and theme overrides work without inline-style conflicts.
const AVATAR_TONE_KEYS = ["blue", "green", "violet", "amber", "teal"] as const;

export type AvatarToneKey = (typeof AVATAR_TONE_KEYS)[number];

export function defaultUserAvatar(name: string) {
  const characters = Array.from(name);
  const colorIndex =
    characters.reduce((total, character) => total + character.codePointAt(0)!, 0) %
    AVATAR_TONE_KEYS.length;
  return {
    initial: (characters[0] || "U").toUpperCase(),
    tone: AVATAR_TONE_KEYS[colorIndex] as AvatarToneKey,
  };
}
