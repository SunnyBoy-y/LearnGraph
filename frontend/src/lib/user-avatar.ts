// Keep the original sidebar palette and stable, name-based color selection.
const AVATAR_TONES = [
  { backgroundColor: "#ddf4ff", color: "#0969da" },
  { backgroundColor: "#dafbe1", color: "#1a7f37" },
  { backgroundColor: "#fbefff", color: "#8250df" },
  { backgroundColor: "#fff1e5", color: "#bc4c00" },
  { backgroundColor: "#ddf7f4", color: "#0f766e" },
];

export function defaultUserAvatar(name: string) {
  const characters = Array.from(name);
  const colorIndex = characters.reduce((total, character) => total + character.codePointAt(0)!, 0) % AVATAR_TONES.length;
  return {
    initial: (characters[0] || "U").toUpperCase(),
    style: AVATAR_TONES[colorIndex],
  };
}
