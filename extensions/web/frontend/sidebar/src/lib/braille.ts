/** Grade-1 braille, `a` through `z`. A letter is substituted with the cell that actually spells it,
 *  so ciphertext a member watches is the message rather than noise shaped like one. */
const BRAILLE_LETTERS = Array.from("⠁⠃⠉⠙⠑⠋⠛⠓⠊⠚⠅⠇⠍⠝⠕⠏⠟⠗⠎⠞⠥⠧⠺⠭⠽⠵");
const BRAILLE_BLOCK = 0x2800;
const BRAILLE_CELLS = 0x100;
const LETTER_A = 97;

export function brailleOf(character: string): string | null {
  const letter = character.toLowerCase().charCodeAt(0) - LETTER_A;
  return letter >= 0 && letter < BRAILLE_LETTERS.length ? BRAILLE_LETTERS[letter] : null;
}

/** The inverse of `brailleOf` over the letters, so a capture of a first frame can be read back and
 *  checked against the message it is hiding. */
export function readBraille(cipher: string): string {
  return Array.from(cipher, (glyph) => {
    const at = BRAILLE_LETTERS.indexOf(glyph);
    return at === -1 ? glyph : String.fromCharCode(LETTER_A + at);
  }).join("");
}

export function randomCell(): string {
  return String.fromCodePoint(BRAILLE_BLOCK + Math.floor(Math.random() * BRAILLE_CELLS));
}

/** The cell any character hides behind, drawn from the character itself so the same word ciphers the
 *  same way every time it is read. A digit or a bracket has no braille of its own and takes a cell
 *  off its own code point; rolling one instead would give a word a new disguise on every frame of
 *  the stream that redraws it. */
export function cipherOf(character: string): string {
  return (
    brailleOf(character) ??
    String.fromCodePoint(BRAILLE_BLOCK + (character.codePointAt(0)! % BRAILLE_CELLS))
  );
}
