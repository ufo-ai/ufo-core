const BRAILLE_LETTERS = Array.from("⠁⠃⠉⠙⠑⠋⠛⠓⠊⠚⠅⠇⠍⠝⠕⠏⠟⠗⠎⠞⠥⠧⠺⠭⠽⠵");
const BRAILLE_BLOCK = 0x2800;
const BRAILLE_CELLS = 0x100;
const LETTER_A = 97;

export function brailleOf(character: string): string | null {
  const letter = character.toLowerCase().charCodeAt(0) - LETTER_A;
  return letter >= 0 && letter < BRAILLE_LETTERS.length ? BRAILLE_LETTERS[letter] : null;
}

export function readBraille(cipher: string): string {
  return Array.from(cipher, (glyph) => {
    const at = BRAILLE_LETTERS.indexOf(glyph);
    return at === -1 ? glyph : String.fromCharCode(LETTER_A + at);
  }).join("");
}

export function randomCell(): string {
  return String.fromCodePoint(BRAILLE_BLOCK + Math.floor(Math.random() * BRAILLE_CELLS));
}

export function cipherOf(character: string): string {
  return (
    brailleOf(character) ??
    String.fromCodePoint(BRAILLE_BLOCK + (character.codePointAt(0)! % BRAILLE_CELLS))
  );
}
