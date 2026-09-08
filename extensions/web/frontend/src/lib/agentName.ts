export function agentName(stored: string): string {
  return stored.replace(/[^\s\-_]+/g, (word) =>
    word === word.toLowerCase() ? word[0].toUpperCase() + word.slice(1) : word,
  );
}
