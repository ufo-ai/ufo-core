use std::collections::HashMap;
use std::sync::Arc;

use anyhow::Context;
use chrono::{Datelike, Utc};
use rcgen::{
    date_time_ymd, BasicConstraints, Certificate, CertificateParams, DistinguishedName, DnType,
    ExtendedKeyUsagePurpose, IsCa, KeyPair, KeyUsagePurpose,
};
use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{ClientConfig, RootCertStore, ServerConfig};
use tokio::sync::Mutex;

const CA_COMMON_NAME: &str = "ufo-sandbox-proxy";
const CA_VALID_DAYS: i64 = 3650;
const LEAF_VALID_DAYS: i64 = 365;
const ALPN_HTTP_1_1: &[u8] = b"http/1.1";

pub fn generate_ca() -> anyhow::Result<(String, String)> {
    let key = KeyPair::generate().context("generate CA key")?;
    let mut params = CertificateParams::new(Vec::<String>::new()).context("CA params")?;
    params.distinguished_name = DistinguishedName::new();
    params
        .distinguished_name
        .push(DnType::CommonName, CA_COMMON_NAME);
    params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    params.key_usages = vec![
        KeyUsagePurpose::KeyCertSign,
        KeyUsagePurpose::CrlSign,
        KeyUsagePurpose::DigitalSignature,
    ];
    set_validity(&mut params, CA_VALID_DAYS);
    let cert = params.self_signed(&key).context("self-sign CA")?;
    Ok((cert.pem(), key.serialize_pem()))
}

pub struct LeafStore {
    ca_cert: Certificate,
    ca_key: KeyPair,
    cache: Mutex<HashMap<String, Arc<ServerConfig>>>,
}

impl LeafStore {
    pub fn new(ca_cert_pem: &str, ca_key_pem: &str) -> anyhow::Result<LeafStore> {
        let ca_key = KeyPair::from_pem(ca_key_pem).context("parse CA key")?;
        let ca_params =
            CertificateParams::from_ca_cert_pem(ca_cert_pem).context("parse CA cert")?;
        let ca_cert = ca_params
            .self_signed(&ca_key)
            .context("reconstruct CA cert for signing")?;
        Ok(LeafStore {
            ca_cert,
            ca_key,
            cache: Mutex::new(HashMap::new()),
        })
    }

    pub async fn server_config(&self, host: &str) -> anyhow::Result<Arc<ServerConfig>> {
        let mut cache = self.cache.lock().await;
        if let Some(config) = cache.get(host) {
            return Ok(config.clone());
        }
        let (leaf_cert, leaf_key) = mint_leaf(&self.ca_cert, &self.ca_key, host)?;
        let config = Arc::new(leaf_server_config(&leaf_cert, &leaf_key)?);
        cache.insert(host.to_string(), config.clone());
        Ok(config)
    }
}

pub fn upstream_client_config() -> Arc<ClientConfig> {
    let mut roots = RootCertStore::empty();
    roots.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .expect("ring provider supports the default protocol versions")
        .with_root_certificates(roots)
        .with_no_client_auth();
    Arc::new(config)
}

fn set_validity(params: &mut CertificateParams, days: i64) {
    let now = Utc::now();
    params.not_before = date_time_ymd(now.year(), now.month() as u8, now.day() as u8);
    let end = now + chrono::Duration::days(days);
    params.not_after = date_time_ymd(end.year(), end.month() as u8, end.day() as u8);
}

fn mint_leaf(
    ca_cert: &Certificate,
    ca_key: &KeyPair,
    host: &str,
) -> anyhow::Result<(Certificate, KeyPair)> {
    let leaf_key = KeyPair::generate().context("generate leaf key")?;
    let mut params = CertificateParams::new(vec![host.to_string()]).context("leaf params")?;
    params.distinguished_name = DistinguishedName::new();
    params.distinguished_name.push(DnType::CommonName, host);
    params.is_ca = IsCa::NoCa;
    params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    set_validity(&mut params, LEAF_VALID_DAYS);
    let leaf_cert = params
        .signed_by(&leaf_key, ca_cert, ca_key)
        .context("sign leaf with CA")?;
    Ok((leaf_cert, leaf_key))
}

fn leaf_server_config(leaf_cert: &Certificate, leaf_key: &KeyPair) -> anyhow::Result<ServerConfig> {
    let cert_chain = vec![leaf_cert.der().clone()];
    let key_der = PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(leaf_key.serialize_der()));
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let mut config = ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .context("server protocol versions")?
        .with_no_client_auth()
        .with_single_cert(cert_chain, key_der)
        .context("load leaf into server config")?;
    config.alpn_protocols = vec![ALPN_HTTP_1_1.to_vec()];
    Ok(config)
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use rustls::pki_types::ServerName;
    use rustls::{ClientConnection, ServerConnection};

    use super::*;

    fn parse_leaf(pem: &str) -> CertificateParams {
        CertificateParams::from_ca_cert_pem(pem).expect("parse minted leaf")
    }

    fn client_trusting(ca_cert: &Certificate) -> Arc<ClientConfig> {
        let mut roots = RootCertStore::empty();
        let (added, ignored) = roots.add_parsable_certificates([ca_cert.der().clone()]);
        assert_eq!((added, ignored), (1, 0));
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let mut config = ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_root_certificates(roots)
            .with_no_client_auth();
        config.alpn_protocols = vec![ALPN_HTTP_1_1.to_vec()];
        Arc::new(config)
    }

    fn pump(client: &mut ClientConnection, server: &mut ServerConnection) -> anyhow::Result<()> {
        for _ in 0..40 {
            let mut outbound = Vec::new();
            while client.write_tls(&mut outbound)? > 0 {}
            if !outbound.is_empty() {
                let mut cursor = Cursor::new(&outbound);
                while (cursor.position() as usize) < outbound.len() {
                    server.read_tls(&mut cursor)?;
                }
                server.process_new_packets()?;
            }
            let mut inbound = Vec::new();
            while server.write_tls(&mut inbound)? > 0 {}
            if !inbound.is_empty() {
                let mut cursor = Cursor::new(&inbound);
                while (cursor.position() as usize) < inbound.len() {
                    client.read_tls(&mut cursor)?;
                }
                client.process_new_packets()?;
            }
            if !client.is_handshaking() && !server.is_handshaking() {
                return Ok(());
            }
        }
        anyhow::bail!("handshake did not complete within the round budget")
    }

    #[test]
    fn generate_ca_produces_parseable_pem() {
        let (cert_pem, key_pem) = generate_ca().unwrap();
        assert!(cert_pem.contains("BEGIN CERTIFICATE"));
        assert!(key_pem.contains("PRIVATE KEY"));
        KeyPair::from_pem(&key_pem).expect("CA key round-trips");
        let params = CertificateParams::from_ca_cert_pem(&cert_pem).expect("CA cert round-trips");
        assert!(matches!(params.is_ca, IsCa::Ca(_)));
        let cn = format!("{:?}", params.distinguished_name.get(&DnType::CommonName));
        assert!(cn.contains(CA_COMMON_NAME), "unexpected CA subject: {cn}");
    }

    #[test]
    fn new_accepts_a_generated_ca() {
        let (cert_pem, key_pem) = generate_ca().unwrap();
        LeafStore::new(&cert_pem, &key_pem).expect("load generated CA");
    }

    #[test]
    fn leaf_carries_san_eku_and_cn() {
        let (cert_pem, key_pem) = generate_ca().unwrap();
        let store = LeafStore::new(&cert_pem, &key_pem).unwrap();
        let (leaf, _key) = mint_leaf(&store.ca_cert, &store.ca_key, "api.example.com").unwrap();
        let params = parse_leaf(&leaf.pem());
        let sans = format!("{:?}", params.subject_alt_names);
        assert!(sans.contains("api.example.com"), "no SAN for host: {sans}");
        assert!(params
            .extended_key_usages
            .contains(&ExtendedKeyUsagePurpose::ServerAuth));
        let cn = format!("{:?}", params.distinguished_name.get(&DnType::CommonName));
        assert!(cn.contains("api.example.com"), "unexpected leaf CN: {cn}");
    }

    #[tokio::test]
    async fn server_config_is_cached_per_host() {
        let (cert_pem, key_pem) = generate_ca().unwrap();
        let store = LeafStore::new(&cert_pem, &key_pem).unwrap();
        let first = store.server_config("api.example.com").await.unwrap();
        let second = store.server_config("api.example.com").await.unwrap();
        assert!(Arc::ptr_eq(&first, &second), "leaf re-minted on cache hit");
        let other = store.server_config("api.openai.com").await.unwrap();
        assert!(
            !Arc::ptr_eq(&first, &other),
            "distinct hosts shared a config"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn concurrent_first_contact_mints_once() {
        let (cert_pem, key_pem) = generate_ca().unwrap();
        let store = Arc::new(LeafStore::new(&cert_pem, &key_pem).unwrap());
        let a = {
            let store = store.clone();
            tokio::spawn(async move { store.server_config("api.example.com").await.unwrap() })
        };
        let b = {
            let store = store.clone();
            tokio::spawn(async move { store.server_config("api.example.com").await.unwrap() })
        };
        let (first, second) = (a.await.unwrap(), b.await.unwrap());
        assert!(
            Arc::ptr_eq(&first, &second),
            "concurrent first-contact minted two leaves"
        );
    }

    #[tokio::test]
    async fn rustls_accepts_the_minted_configs() {
        let (cert_pem, key_pem) = generate_ca().unwrap();
        let store = LeafStore::new(&cert_pem, &key_pem).unwrap();
        let server_config = store.server_config("api.example.com").await.unwrap();
        assert_eq!(server_config.alpn_protocols, vec![ALPN_HTTP_1_1.to_vec()]);

        let client_config = client_trusting(&store.ca_cert);
        let mut client = ClientConnection::new(
            client_config,
            ServerName::try_from("api.example.com").unwrap(),
        )
        .unwrap();
        let mut server = ServerConnection::new(server_config).unwrap();
        pump(&mut client, &mut server).expect("handshake");
        assert!(!client.is_handshaking() && !server.is_handshaking());
        assert_eq!(client.alpn_protocol(), Some(ALPN_HTTP_1_1));
    }

    #[tokio::test]
    async fn wrong_host_is_rejected_by_rustls() {
        let (cert_pem, key_pem) = generate_ca().unwrap();
        let store = LeafStore::new(&cert_pem, &key_pem).unwrap();
        let server_config = store.server_config("api.example.com").await.unwrap();
        let client_config = client_trusting(&store.ca_cert);
        let mut client = ClientConnection::new(
            client_config,
            ServerName::try_from("evil.example.com").unwrap(),
        )
        .unwrap();
        let mut server = ServerConnection::new(server_config).unwrap();
        assert!(
            pump(&mut client, &mut server).is_err(),
            "leaf for one host validated against another"
        );
    }

    #[test]
    fn upstream_client_config_builds_with_web_roots() {
        let config = upstream_client_config();
        assert!(!config.crypto_provider().kx_groups.is_empty());
    }

    // The hosted CA arrives as PKCS#8, the one encoding `KeyPair::from_pem` parses, so the deploy
    // hands over `private_key_pem_pkcs8` rather than terraform's PKCS#1 default.
    const RSA_PKCS8_CA_KEY: &str = "-----BEGIN PRIVATE KEY-----\n\
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDmh+MtXuT2DwL2\n\
wOOIepJ1K1DaJQ68i3jUiHW+hDIdmmqCi6zB3CBfMsSCzG6dBLS+5gIrcV5QNyqv\n\
ciKgg+L5k1lnYReVEKIPrsNAIpctah44s7M++JTgSBBg3Nenbs5jAJdttylW8J8Q\n\
NqzOUCniGGr1Ga0M7yftZeSWWKwPyDqyrYX5PeGFBR63zE9ZOuG7oGlqMic+GZf7\n\
3sufOo3KL7QjIy+GpnRksc3APw+PU1RCdGYzB0WTDkSdPThmO1taK7pHafjZDTno\n\
BL7Et15Yg7ypXiHUDoHVTN2qtZLFZO8oG9WjrjrftttRTfrBpaJ+dHLeABw2rERH\n\
igOM1lefAgMBAAECggEACM1YIOuw+wLYY8rk+oQQ7YUJjEVtSkkFBGXFdnseXWpU\n\
9hvBLTxrcVcYkRSfMJhKZ8jpJ6F82WaR1wBuTrSvyO8VsyXOPF8rNkVqUnDqVs3o\n\
ziobsPmALblyrqV1XSuU7nYfp2Jz+KGCtqAaebIujIx5dlb++DHK03ytfV7GCxV7\n\
4uny6CzYnSzrub61l6rh4hz+a28ak8nKxNzDt4K41msgudxjYm3p8SFbjm/HPbUS\n\
tC4p8XbzN12266Bi0J0pQqrVdLjeMgT42/Ys/qz5CH5znMr52Xh/pW9rT1FFesxz\n\
irms/f52t7C7Ip4AIlPMlL6BDSQoFCBlby7dKT28aQKBgQD0/CnZ9obgKrxY/rab\n\
GWrhD4fcqLbqKmu2KNtYwgeHet/Y6nsp39iS7+ChjWxhDFMk9rJV800ZVZo5I5aM\n\
wVR5+nm2XzqOX/kVS/VEE0/D07YckjoWs0pqU7IeyPTwd+PnTjp4SRdYuzv0r8Jy\n\
+qCqFk3qiAj4KC8vmADKsZqQwwKBgQDw5VpBxY/g2grAeLDU2Inm25yjfegoWqzE\n\
mbyPg+koxaXPuvJN3PYDp9YdhZZSJjZl70nPFbmgL9qXxJviI2vAsCGVMNKIJx2Z\n\
/mfXOb7u6AXqgku0vL6oYFCRHEeGtRyIZUyTGPcucibKU0Sopt5Xu7DEUpFte3Zo\n\
YTVdifIv9QKBgFAU6d1dD+PRNHZm9OwoV96wA/pmkDxll4YZPvJ5oppv2SKAK7iZ\n\
eqM1lJlasHXc4ITxu4QLH0XLzLkm3/ys6d9huE2cPXjy+Go0xTz2jxl1aE9YoXJw\n\
M0AkkdIsYJ0Go9IlqUlOozoXIlcu6QJK2SAgYGHtC/mKsTn+lyuq+NqfAoGAH15e\n\
UG4/fBIokEOnEzBXVL6IOSnuD0MveDJkwXN16x9Bpjk70DPTvUofsZxxpKThNIji\n\
XZsAnwFcP6MUgXAHWgIVfW3sHFqrmh/subQFTurbylvJK/HgCeDw3NSH49y1qHU4\n\
cXcwyNWIg5QwPp3sGhSQwh/WXCFVm+X8ov+Rj/kCgYEAvWrpxSQIrsj+MkXILdec\n\
eVD7gfDb41EvZqAlhc6jxN+G02Zh6+LMWM7+N/8TA0GqfpX8KXnlHZJgGKwOlEP7\n\
/GrCXNYpgPf5UClyVlOAM5dMFWARG0WdxDXdyDS/EYLb6FxOzl+YcOlUtz8KK9vb\n\
WeKB3t3RoBP9PjD5ThLLhBU=\n\
-----END PRIVATE KEY-----\n";

    const RSA_CA_CERT: &str = "-----BEGIN CERTIFICATE-----\n\
MIIDGzCCAgOgAwIBAgIULu5CsTE8nxaZ7HkzXmqK7YW6b6MwDQYJKoZIhvcNAQEL\n\
BQAwHTEbMBkGA1UEAwwSdWZvLWVncmVzcy10ZXN0LWNhMB4XDTI2MDgxNzA5MTk1\n\
MFoXDTM2MDgxNDA5MTk1MFowHTEbMBkGA1UEAwwSdWZvLWVncmVzcy10ZXN0LWNh\n\
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA5ofjLV7k9g8C9sDjiHqS\n\
dStQ2iUOvIt41Ih1voQyHZpqgouswdwgXzLEgsxunQS0vuYCK3FeUDcqr3IioIPi\n\
+ZNZZ2EXlRCiD67DQCKXLWoeOLOzPviU4EgQYNzXp27OYwCXbbcpVvCfEDaszlAp\n\
4hhq9RmtDO8n7WXkllisD8g6sq2F+T3hhQUet8xPWTrhu6BpajInPhmX+97LnzqN\n\
yi+0IyMvhqZ0ZLHNwD8Pj1NUQnRmMwdFkw5EnT04ZjtbWiu6R2n42Q056AS+xLde\n\
WIO8qV4h1A6B1UzdqrWSxWTvKBvVo64637bbUU36waWifnRy3gAcNqxER4oDjNZX\n\
nwIDAQABo1MwUTAdBgNVHQ4EFgQU/otcG8+eyojkW1ARDnD9e1F1UcUwHwYDVR0j\n\
BBgwFoAU/otcG8+eyojkW1ARDnD9e1F1UcUwDwYDVR0TAQH/BAUwAwEB/zANBgkq\n\
hkiG9w0BAQsFAAOCAQEAVb9d6938zSPRXpMEKC2tkRTpFugWfauHS8b5igLj9FOa\n\
TRnhWweCunupdI1pkhgV7cqpT3PrN1Wo6lXM9p1N3usBSoilDOBet37zBFCWk1iK\n\
Cv9y71Q9FS4s5lQG9MR6pj6c9nWASbzL/TZcOqdiqQesuoP7btTC94L1aXJAJx9H\n\
8TGhtSyOEWGNFKvk09WzqAv5b6EeRIDix8fBrHriyblZ+JN+F+ITnmwsAXqtVoop\n\
KuDmYWYfNKMksP1q9AKposIiG2G4dMhm/Uz3QH17g6Qk7+AzeBTY9vHSKRRtxDsq\n\
YOlTysCSLFRZbtf5IuFNEoSYi/dZtUgidBOJYwlQaQ==\n\
-----END CERTIFICATE-----\n";

    #[tokio::test]
    async fn new_accepts_an_rsa_pkcs8_ca_the_way_the_hosted_deploy_provisions_it() {
        let store = LeafStore::new(RSA_CA_CERT, RSA_PKCS8_CA_KEY).expect("load RSA PKCS#8 CA");
        store
            .server_config("api.example.com")
            .await
            .expect("mint a leaf under the RSA CA");
    }
}
