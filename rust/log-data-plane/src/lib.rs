pub mod bundle;
pub mod engine;
pub mod mapping;
pub mod normalization;
pub mod parser;
pub mod taxonomy;

use serde::Serialize;
use serde_json::Value;

use crate::engine::{FamilyEngine, ProcessResult};

#[derive(Debug, Serialize)]
pub struct GoldenReport {
    pub ok: bool,
    pub cases: usize,
    pub matched: usize,
    pub mismatches: Vec<GoldenMismatch>,
}

#[derive(Debug, Serialize)]
pub struct GoldenMismatch {
    pub observation: usize,
    pub fields: Vec<String>,
    pub actual: ProcessResult,
}

pub fn verify_golden(engine: &FamilyEngine, golden: &Value) -> Result<GoldenReport, String> {
    if golden["compatibility_hash"].as_str() != Some(engine.bundle.compatibility_hash.as_str()) {
        return Err("golden_compatibility_hash_mismatch".to_string());
    }
    let cases = golden["cases"]
        .as_array()
        .ok_or_else(|| "invalid_golden_cases".to_string())?;
    let mut mismatches = Vec::new();
    let mut matched = 0;
    for (index, case) in cases.iter().enumerate() {
        let raw = case["raw"]
            .as_str()
            .ok_or_else(|| "invalid_golden_raw".to_string())?;
        let actual = engine.process(raw);
        let differences = result_differences(&actual, &case["expected"]);
        if differences.is_empty() {
            matched += 1;
        } else {
            mismatches.push(GoldenMismatch {
                observation: index + 1,
                fields: differences,
                actual,
            });
        }
    }
    Ok(GoldenReport {
        ok: mismatches.is_empty(),
        cases: cases.len(),
        matched,
        mismatches,
    })
}

fn result_differences(actual: &ProcessResult, expected: &Value) -> Vec<String> {
    let mut differences = Vec::new();
    if expected["ok"].as_bool() != Some(actual.ok) {
        differences.push("ok".to_string());
    }
    if expected["status"].as_str() != Some(actual.status.as_str()) {
        differences.push("status".to_string());
    }
    for (field, actual_value) in [
        ("captures", serde_json::to_value(&actual.captures).unwrap()),
        (
            "normalized",
            serde_json::to_value(&actual.normalized).unwrap(),
        ),
        ("event", serde_json::to_value(&actual.event).unwrap()),
    ] {
        if expected.get(field) != Some(&actual_value) {
            differences.push(field.to_string());
        }
    }
    differences
}
