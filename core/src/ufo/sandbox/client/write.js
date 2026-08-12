function refusal(message) {
  const error = new Error(message);
  error.refused = true;
  return error;
}

function normalized(path) {
  const parts = [];
  for (const part of path.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === ".." && parts.length && parts[parts.length - 1] !== "..") parts.pop();
    else parts.push(part);
  }
  return (path.slice(0, 1) === "/" ? "/" : "") + parts.join("/");
}

function landed(params) {
  const target = params.path;
  const staged = params.staged_path;
  makeParents(target);
  if (normalized(staged) === normalized(target)) {
    throw refusal("staged file must differ from its target");
  }
  const existing = statMode(target);
  try {
    if (existing !== null && params.allow_existing !== true) {
      throw refusal("file " + target + " must be read before it is written");
    }
    if (statMode(staged) === null) throw refusal(staged + " not found");
    if (existing !== null) setMode(staged, existing);
    try {
      rename(staged, target);
    } catch (error) {
      throw refusal(error.message);
    }
    return { created: existing === null };
  } finally {
    unlink(staged);
  }
}

function main(params) {
  try {
    print(JSON.stringify(landed(params)));
  } catch (error) {
    if (!error || !error.refused) throw error;
    print(JSON.stringify({ error: error.message }));
  }
}
