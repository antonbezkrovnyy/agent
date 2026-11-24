use std::fs;
use std::path::Path;

use log_data_plane::bundle::FamilyBundle;
use log_data_plane::engine::{FamilyEngine, FamilyRegistry};
use log_data_plane::verify_golden;
use serde_json::Value;

fn artifact(name: &str) -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../research")
        .join(name)
}

#[test]
fn cisco_results_match_python_golden() {
    let bundle = FamilyBundle::from_path(&artifact("r030-cisco-acl-verdict.bundle.json")).unwrap();
    let golden: Value = serde_json::from_str(
        &fs::read_to_string(artifact("r030-cisco-acl-verdict.golden.json")).unwrap(),
    )
    .unwrap();
    let engine = FamilyEngine::new(bundle).unwrap();
    let report = verify_golden(&engine, &golden).unwrap();
    assert!(report.ok, "{:#?}", report.mismatches);
    assert_eq!(report.matched, 4);
}

#[test]
fn registry_uses_dispatch_and_rejects_unknown_raw() {
    let bundle = FamilyBundle::from_path(&artifact("r030-cisco-acl-verdict.bundle.json")).unwrap();
    let evidence = bundle.mapping.evidence[0].clone();
    let registry = FamilyRegistry::new(vec![bundle]).unwrap();
    assert_eq!(registry.candidates(&evidence).len(), 1);
    assert!(registry.process(&evidence).ok);
    assert!(registry.candidates("unrelated log").is_empty());
    assert_eq!(registry.process("unrelated log").status, "no_family");
}

#[test]
fn bundle_hash_detects_modification() {
    let text = fs::read_to_string(artifact("r030-cisco-acl-verdict.bundle.json")).unwrap();
    let mut value: Value = serde_json::from_str(&text).unwrap();
    value["mapping"]["source_timezone"] = Value::String("Europe/Moscow".to_string());
    let error = FamilyBundle::from_json(&value.to_string()).unwrap_err();
    assert!(error.to_string().contains("compatibility_hash_mismatch"));
}
