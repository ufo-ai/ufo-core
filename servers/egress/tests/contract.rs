//! The rule wire contract, pinned end to end: the fixture is produced by core `serve`'s
//! `rule_json` (Python), and this test deserializes it into the Rust `Rule` enum. A drift in the
//! `kind` tag, a field name, or the `hosts`/`daemon_prefix` renames fails here rather than on the
//! wire against a live control plane.

use std::collections::BTreeSet;

use ufo_egress::types::Rule;

#[derive(serde::Deserialize)]
struct Fixture {
    rules: Vec<Rule>,
}

#[test]
fn python_rule_contract_deserializes_to_the_rust_rule_enum() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/rule_contract.json");
    let json = std::fs::read_to_string(path).expect("read rule_contract.json");
    let fixture: Fixture = serde_json::from_str(&json).expect("deserialize the rule contract");
    assert_eq!(fixture.rules.len(), 6, "one of each rule variant");

    assert!(matches!(
        &fixture.rules[0],
        Rule::Scope { allowed_hosts }
            if allowed_hosts == &BTreeSet::from([
                "api.anthropic.com".to_string(),
                "api.openai.com".to_string(),
            ])
    ));
    assert!(matches!(&fixture.rules[1], Rule::Internet));
    assert!(matches!(
        &fixture.rules[2],
        Rule::Injection { host, header, sentinel, real }
            if host == "api.anthropic.com"
                && header == "x-api-key"
                && sentinel == "UFO_SENTINEL_MODEL_KEY"
                && real == "sk-ant-real-key"
    ));
    assert!(matches!(
        &fixture.rules[3],
        Rule::Meter { host, dimension } if host == "api.anthropic.com" && dimension == "tokens"
    ));
    assert!(matches!(
        &fixture.rules[4],
        Rule::Forward { host, header, sentinel, account_id }
            if host == "api.github.com"
                && header == "authorization"
                && sentinel == "UFO_SENTINEL_GRANT_acct-9f3c"
                && account_id == "acct-9f3c"
    ));
    assert!(matches!(
        &fixture.rules[5],
        Rule::Service { host, daemon_prefix }
            if host == "registry.npmjs.org"
                && daemon_prefix.as_deref() == Some("/pkg/registry.npmjs.org")
    ));
}
