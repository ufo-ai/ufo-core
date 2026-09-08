/** An agent's name as a member reads it: a word takes a capital exactly when the whole word is
 *  lowercase — `assistant` reads `Assistant`, `code reviewer` reads `Code Reviewer`, and
 *  `iOS helper` reads `iOS Helper`, because a word the member capitalized inside is the shape they
 *  chose and is never rewritten. A hyphen and an underscore part words the way a space does, so the
 *  slug an extension ships its agent under reads `Daily-Brief` rather than half-drawn.
 *
 *  Rendered text only. The stored name is the agent's identity: it addresses the object in an
 *  intent, it is the key the rail groups conversations under, and it is what search matches — so
 *  every write and every comparison carries the name as stored, and this is drawn beside them. */
export function agentName(stored: string): string {
  return stored.replace(/[^\s\-_]+/g, (word) =>
    word === word.toLowerCase() ? word[0].toUpperCase() + word.slice(1) : word,
  );
}
