function quoted(word) {
  return "'" + String(word).replace(/'/g, "'\\''") + "'";
}

function hex(bytes) {
  const parts = new Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) parts[i] = bytes[i].toString(16).padStart(2, "0");
  return parts.join("");
}

function emit(p) {
  const lines = ["export UFO_OP_WORKDIR=" + quoted(WORKDIR)];
  const env = p.env || {};
  const cert = env.UFO_EGRESS_CA_CERT;
  for (const name of Object.keys(env)) {
    if (name === "UFO_EGRESS_CA_CERT") continue;
    lines.push("export " + name + "=" + quoted(env[name]));
  }
  if (cert) {
    writeFile(WORKDIR + "/egress-ca.pem", cert);
    for (const name of ["SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"]) {
      lines.push("export " + name + "=" + quoted(WORKDIR + "/egress-ca.pem"));
    }
  }
  lines.push(p.argv.map(quoted).join(" "));
  print(lines.join("\n"));
}

function pack() {
  print(JSON.stringify({
    exit_code: Number(ARGS[2]),
    stdout_hex: hex(readFile(ARGS[0], "binary")),
    stderr_hex: hex(readFile(ARGS[1], "binary")),
  }));
}

function main(p) {
  if (MODE === "emit") emit(p);
  else pack();
}
