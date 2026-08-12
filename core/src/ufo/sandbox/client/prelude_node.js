const fs = require("node:fs");

let MODE = "";
let WORKDIR = "";
let ARGS = [];

function __start__(argv) {
  MODE = String(argv[0] || "");
  WORKDIR = String(argv[1] || "");
  ARGS = argv.slice(2).map(String);
}

function print(line) {
  process.stdout.write(String(line) + "\n");
}

function __latin1__(path) {
  return fs.readFileSync(path).toString("latin1");
}

function readFile(path, mode) {
  const buffer = fs.readFileSync(path);
  if (mode !== "binary") return buffer.toString("latin1");
  return new Uint8Array(buffer);
}

function writeFile(path, content) {
  const data = typeof content === "string" ? Buffer.from(content, "latin1") : Buffer.from(content);
  fs.writeFileSync(path, data);
}

function statMode(path) {
  try {
    return fs.lstatSync(path).mode & 0o777;
  } catch {
    return null;
  }
}

function setMode(path, mode) {
  try {
    fs.chmodSync(path, mode & 0o777);
  } catch {
    throw new Error("Could not set mode on " + path);
  }
}

function rename(from, to) {
  try {
    fs.renameSync(from, to);
  } catch {
    throw new Error("Could not rename " + from + " onto " + to);
  }
}

function unlink(path) {
  try {
    fs.unlinkSync(path);
  } catch {
    return;
  }
}

function makeParents(path) {
  const cut = path.lastIndexOf("/");
  if (cut <= 0) return;
  try {
    fs.mkdirSync(path.slice(0, cut), { recursive: true });
  } catch {
    return;
  }
}
