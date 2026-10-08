use base64::engine::general_purpose::STANDARD;
use base64::Engine;

const PEM_LINE_BYTES: usize = 64;
pub(crate) const X509_OVERRIDE: &str = "x509sslcertoverrideplatform";
// libcurl tools (git, cargo) ignore CURL_CA_BUNDLE when they set their own CAINFO, so each needs its
// own override or a MITM'd host fails with unable to get local issuer certificate.
pub(crate) const CA_CERT_CONSUMERS: [&str; 6] = [
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "GIT_SSL_CAINFO",
    "CARGO_HTTP_CAINFO",
];

pub(crate) fn trust_bundle(ca_cert: &str) -> Result<String, String> {
    let roots = rustls_native_certs::load_native_certs()
        .map_err(|error| format!("could not read this machine's trust store: {error}"))?;
    if roots.is_empty() {
        return Err("this machine's trust store holds no certificates".into());
    }
    let mut bundle: String = roots.iter().map(|root| pem(root.as_ref())).collect();
    bundle.push_str(ca_cert);
    if !bundle.ends_with('\n') {
        bundle.push('\n');
    }
    Ok(bundle)
}

fn pem(certificate: &[u8]) -> String {
    let encoded = STANDARD.encode(certificate);
    let mut out = String::from("-----BEGIN CERTIFICATE-----\n");
    for line in encoded.as_bytes().chunks(PEM_LINE_BYTES) {
        out.push_str(std::str::from_utf8(line).expect("base64 is ascii"));
        out.push('\n');
    }
    out.push_str("-----END CERTIFICATE-----\n");
    out
}

pub(crate) fn godebug(current: Option<&str>) -> String {
    let set = format!("{X509_OVERRIDE}=1");
    current
        .into_iter()
        .flat_map(|value| value.split(','))
        .filter(|setting| {
            !setting.is_empty()
                && setting
                    .split_once('=')
                    .is_none_or(|(name, _)| name != X509_OVERRIDE)
        })
        .chain(std::iter::once(set.as_str()))
        .collect::<Vec<_>>()
        .join(",")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trust_bundle_carries_this_machine_and_the_proxy_ca() {
        let bundle =
            trust_bundle("-----BEGIN CERTIFICATE-----\nPROXYCA\n-----END CERTIFICATE-----\n")
                .unwrap();
        assert!(bundle.matches("BEGIN CERTIFICATE").count() > 1);
        assert!(bundle.ends_with("PROXYCA\n-----END CERTIFICATE-----\n"));
    }

    #[test]
    fn go_uses_the_bundle_without_dropping_other_debug_settings() {
        assert_eq!(
            godebug(Some("http2debug=1,x509sslcertoverrideplatform=0")),
            "http2debug=1,x509sslcertoverrideplatform=1"
        );
        assert_eq!(godebug(None), "x509sslcertoverrideplatform=1");
    }

    #[test]
    fn every_root_encodes_as_a_readable_certificate() {
        let roots = rustls_native_certs::load_native_certs().unwrap();
        for root in &roots {
            let encoded = pem(root.as_ref());
            let body: String = encoded
                .lines()
                .filter(|line| !line.starts_with("-----"))
                .collect();
            assert!(encoded
                .lines()
                .all(|line| line.len() <= PEM_LINE_BYTES || line.starts_with("-----")));
            assert_eq!(STANDARD.decode(body).unwrap(), root.as_ref());
        }
    }
}
