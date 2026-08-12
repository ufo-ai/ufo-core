ObjC.import("Foundation");
ObjC.bindFunction("rename", ["int", ["string", "string"]]);
ObjC.bindFunction("chmod", ["int", ["string", "int"]]);

let MODE = "";
let WORKDIR = "";
let ARGS = [];

function __start__(argv) {
  MODE = String(argv[0] || "");
  WORKDIR = String(argv[1] || "");
  ARGS = argv.slice(2).map(String);
}

function print(line) {
  $.NSFileHandle.fileHandleWithStandardOutput.writeData(
    $(String(line) + "\n").dataUsingEncoding($.NSUTF8StringEncoding)
  );
}

function __latin1__(path) {
  const data = $.NSData.dataWithContentsOfFile(path);
  if (data.isNil()) throw new Error("Could not open file: " + path);
  return $.NSString.alloc.initWithDataEncoding(data, $.NSISOLatin1StringEncoding).js;
}

function readFile(path, mode) {
  const text = __latin1__(path);
  if (mode !== "binary") return text;
  const bytes = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
  return bytes;
}

function writeFile(path, content) {
  let text = content;
  if (typeof text !== "string") {
    const parts = [];
    for (let i = 0; i < content.length; i += 0x8000) {
      parts.push(String.fromCharCode.apply(null, content.subarray(i, i + 0x8000)));
    }
    text = parts.join("");
  }
  const data = $(text).dataUsingEncodingAllowLossyConversion(
    $.NSISOLatin1StringEncoding,
    false
  );
  const open = $.NSFileHandle.fileHandleForWritingAtPath(path);
  if (!open.isNil()) {
    open.truncateFileAtOffset(0);
    open.writeData(data);
    open.closeAndReturnError($());
    return;
  }
  if (!$.NSFileManager.defaultManager.createFileAtPathContentsAttributes(path, data, $())) {
    throw new Error("Could not write file: " + path);
  }
}

function statMode(path) {
  const attributes = $.NSFileManager.defaultManager.attributesOfItemAtPathError(path, $());
  if (attributes.isNil()) return null;
  const mode = attributes.objectForKey($.NSFilePosixPermissions);
  return mode.isNil() ? null : Number(mode.integerValue) & 0o777;
}

function setMode(path, mode) {
  if ($.chmod(path, mode & 0o777) !== 0) throw new Error("Could not set mode on " + path);
}

function rename(from, to) {
  if ($.rename(from, to) !== 0) throw new Error("Could not rename " + from + " onto " + to);
}

function unlink(path) {
  $.NSFileManager.defaultManager.removeItemAtPathError(path, $());
}

function makeParents(path) {
  const cut = path.lastIndexOf("/");
  if (cut <= 0) return;
  $.NSFileManager.defaultManager.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(
    path.slice(0, cut),
    true,
    $(),
    $()
  );
}
