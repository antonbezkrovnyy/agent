use std::{env, fs, path::Path, process::ExitCode};

use mpsiem::{grok::text::GrokText, normalizer, normalizer::NormalizerStatus, rule::Rules};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use vrl::value::ObjectMap;

fn read_json(path: &str) -> Result<Value, String> {
    let data = fs::read_to_string(path).map_err(|error| format!("read {path}: {error}"))?;
    serde_json::from_str(&data).map_err(|error| format!("parse JSON {path}: {error}"))
}

fn event_map(value: &Value) -> Result<ObjectMap, String> {
    let event = value.as_object().ok_or("fixture event must be an object")?;
    let encoded = serde_json::to_vec(event).map_err(|error| error.to_string())?;
    serde_json::from_slice(&encoded).map_err(|error| format!("convert fixture event: {error}"))
}

fn capabilities() -> Result<Value, String> {
    let describe = |functions: &[Box<dyn vrl::prelude::Function>]| {
        functions
            .iter()
            .map(|function| {
                json!({
                    "name": function.identifier(),
                    "parameters": function.parameters().iter().map(|parameter| json!({
                        "keyword": parameter.keyword,
                        "required": parameter.required,
                    })).collect::<Vec<_>>(),
                })
            })
            .collect::<Vec<_>>()
    };
    let mp = describe(&mpsiem::mpsiemlib::mpsiemlib_all());
    let vrl = describe(&vrl::stdlib::all());
    Ok(json!({
        "ok": true,
        "mp_functions": mp,
        "vrl_functions": vrl,
        "mp_function_count": mp.len(),
        "vrl_function_count": vrl.len(),
    }))
}

fn compare_expected(actual: &Map<String, Value>, expected: &Map<String, Value>) -> Vec<Value> {
    let mut mismatches = Vec::new();
    for (field, expected_value) in expected {
        match actual.get(field) {
            Some(actual_value) if actual_value == expected_value => {}
            Some(actual_value) => mismatches.push(json!({
                "field": field,
                "error": "value_mismatch",
                "expected": expected_value,
                "actual": actual_value,
            })),
            None => mismatches.push(json!({
                "field": field,
                "error": "missing_expected_field",
                "expected": expected_value,
            })),
        }
    }
    mismatches
}

fn fixture_result(mut base: Map<String, Value>, extra: Value) -> Value {
    base.extend(extra.as_object().cloned().unwrap_or_default());
    Value::Object(base)
}

fn execute_fixture(
    normalizer: &normalizer::Normalizer,
    fixture: &Value,
    positive: bool,
    index: usize,
) -> Value {
    let name = fixture.get("name").and_then(Value::as_str).unwrap_or("unnamed");
    let base = json!({"index": index, "name": name, "positive": positive})
        .as_object()
        .cloned()
        .unwrap_or_default();
    let Some(event_value) = fixture.get("event") else {
        return fixture_result(base, json!({"ok": false, "error": "missing_event"}));
    };
    let event = match event_map(event_value) {
        Ok(event) => event,
        Err(error) => {
            return fixture_result(base, json!({"ok": false, "error": error}));
        }
    };
    match normalizer.normalize_raw_event(event) {
        Ok(output) => {
            let actual = serde_json::to_value(output).unwrap_or(Value::Null);
            if !positive {
                return fixture_result(base, json!({
                    "ok": false,
                    "error": "negative_fixture_unexpectedly_parsed",
                    "actual": actual,
                }));
            }
            let actual_object = actual.as_object().cloned().unwrap_or_default();
            let expected = fixture
                .get("expected")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let mut errors = compare_expected(&actual_object, &expected);
            for field in fixture
                .get("required_fields")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
            {
                if let Some(field_name) = field.as_str() {
                    if !actual_object.contains_key(field_name) {
                        errors.push(json!({"field": field_name, "error": "missing_required_field"}));
                    }
                }
            }
            for field in fixture
                .get("unexpected_fields")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
            {
                if let Some(field_name) = field.as_str() {
                    if actual_object.contains_key(field_name) {
                        errors.push(json!({"field": field_name, "error": "unexpected_field_present"}));
                    }
                }
            }
            fixture_result(base, json!({
                "ok": errors.is_empty(),
                "errors": errors,
                "actual": actual,
            }))
        }
        Err(status) => {
            let (status_name, detail) = match status {
                NormalizerStatus::Failure(message) => ("failure", Some(message)),
                NormalizerStatus::Unparsed => ("unparsed", None),
                NormalizerStatus::Skipped => ("skipped", None),
            };
            let expected_status = fixture.get("status").and_then(Value::as_str).unwrap_or("unparsed");
            fixture_result(base, json!({
                "ok": !positive && status_name == expected_status,
                "status": status_name,
                "error": if positive { Some("positive_fixture_not_parsed") } else { None },
                "detail": detail,
            }))
        }
    }
}

fn validate(rules_path: &str, fixtures_path: Option<&str>) -> Result<Value, String> {
    if !Path::new(rules_path).is_file() {
        return Err(format!("rules file not found: {rules_path}"));
    }
    let yaml = fs::read_to_string(rules_path).map_err(|error| error.to_string())?;
    let parsed: Rules = serde_yaml::from_str(&yaml).map_err(|error| error.to_string())?;
    let declared_rules = parsed.rules.len();
    let config = normalizer::Config {
        tag_on_normalizer_failure: "normalizer_failure".to_string(),
        tag_on_unparsed: "unparsed".to_string(),
        body_field: "message".to_string(),
        mime_type_field: "mime".to_string(),
    };
    let normalizer = normalizer::from_yaml(rules_path, &config, true)
        .map_err(|error| format!("strict native load failed: {error}"))?;

    let mut results = Vec::new();
    let mut positive_count = 0;
    let mut negative_count = 0;
    if let Some(path) = fixtures_path {
        let fixtures = read_json(path)?;
        for (index, fixture) in fixtures
            .get("positive")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .iter()
            .enumerate()
        {
            positive_count += 1;
            results.push(execute_fixture(&normalizer, fixture, true, index));
        }
        for (index, fixture) in fixtures
            .get("negative")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .iter()
            .enumerate()
        {
            negative_count += 1;
            results.push(execute_fixture(&normalizer, fixture, false, index));
        }
    }
    let failed = results.iter().filter(|result| result.get("ok") != Some(&Value::Bool(true))).count();
    Ok(json!({
        "ok": failed == 0,
        "strict_load": true,
        "declared_rules": declared_rules,
        "loaded_rules": declared_rules,
        "skipped_rules": 0,
        "positive_fixtures": positive_count,
        "negative_fixtures": negative_count,
        "failed_fixtures": failed,
        "results": results,
    }))
}

#[derive(Deserialize)]
struct CaptureRules {
    rules: Vec<CaptureRule>,
}

#[derive(Deserialize)]
struct CaptureRule {
    name: String,
    pattern: String,
    #[serde(default)]
    positive: Vec<CaptureFixture>,
    #[serde(default)]
    negative: Vec<CaptureFixture>,
}

#[derive(Deserialize)]
struct CaptureFixture {
    name: Option<String>,
    raw: String,
    expected: Option<Map<String, Value>>,
}

fn value_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

fn capture_fixture_result(
    grok: &GrokText,
    rule_id: usize,
    fixture: &CaptureFixture,
    positive: bool,
    index: usize,
) -> Value {
    let search = grok.search(&fixture.raw);
    let matched_rules = search
        .as_ref()
        .map(|result| result.iter().map(|(id, _)| *id).collect::<Vec<_>>())
        .unwrap_or_default();
    let actual = search
        .as_ref()
        .and_then(|result| result.get(&rule_id))
        .and_then(|rule_result| {
            let mut captures = rule_result.capture()?;
            rule_result.postcapture(&mut captures);
            serde_json::to_value(captures).ok()
        })
        .unwrap_or_else(|| json!({}));
    let mut normalized_actual = Map::new();
    for (key, value) in actual.as_object().cloned().unwrap_or_default() {
        normalized_actual.insert(key.strip_prefix('$').unwrap_or(&key).to_string(), value);
    }
    let mut errors = Vec::new();
    if positive && !matched_rules.contains(&rule_id) {
        errors.push(json!({"error": "pattern_not_matched"}));
    }
    if !positive && !matched_rules.is_empty() {
        errors.push(json!({"error": "negative_pattern_matched", "matched_rules": matched_rules}));
    }
    if positive && matched_rules.len() > 1 {
        errors.push(json!({"error": "ambiguous_pattern_match", "matched_rules": matched_rules}));
    }
    if positive {
        for (field, expected) in fixture.expected.clone().unwrap_or_default() {
            match normalized_actual.get(&field) {
                None => errors.push(json!({"field": field, "error": "missing_capture"})),
                Some(actual_value) if value_text(actual_value) != value_text(&expected) => {
                    errors.push(json!({
                        "field": field,
                        "error": "capture_mismatch",
                        "expected": expected,
                        "actual": actual_value,
                    }))
                }
                _ => {}
            }
        }
    }
    json!({
        "index": index,
        "name": fixture.name,
        "positive": positive,
        "ok": errors.is_empty(),
        "matched_rules": matched_rules,
        "captures": normalized_actual,
        "errors": errors,
    })
}

fn captures(path: &str) -> Result<Value, String> {
    let input: CaptureRules = serde_json::from_value(read_json(path)?)
        .map_err(|error| format!("parse capture rules: {error}"))?;
    let mut grok = GrokText::default();
    for (rule_id, rule) in input.rules.iter().enumerate() {
        grok.append(&rule.pattern, rule_id)
            .map_err(|error| format!("compile TEXT pattern {}: {error}", rule.name))?;
    }
    grok.compile().map_err(|error| format!("finalize TEXT patterns: {error}"))?;
    let mut results = Vec::new();
    for (rule_id, rule) in input.rules.iter().enumerate() {
        for (index, fixture) in rule.positive.iter().enumerate() {
            let mut result = capture_fixture_result(&grok, rule_id, fixture, true, index);
            if let Some(object) = result.as_object_mut() {
                object.insert("rule".to_string(), json!(rule.name));
            }
            results.push(result);
        }
        for (index, fixture) in rule.negative.iter().enumerate() {
            let mut result = capture_fixture_result(&grok, rule_id, fixture, false, index);
            if let Some(object) = result.as_object_mut() {
                object.insert("rule".to_string(), json!(rule.name));
            }
            results.push(result);
        }
    }
    let failed = results.iter().filter(|result| result.get("ok") != Some(&Value::Bool(true))).count();
    Ok(json!({
        "ok": failed == 0,
        "rules": input.rules.len(),
        "fixtures": results.len(),
        "failed_fixtures": failed,
        "results": results,
    }))
}

fn run() -> Result<Value, String> {
    let arguments: Vec<String> = env::args().skip(1).collect();
    match arguments.as_slice() {
        [command] if command == "capabilities" => capabilities(),
        [command, patterns] if command == "captures" => captures(patterns),
        [command, rules] if command == "validate" => validate(rules, None),
        [command, rules, fixtures] if command == "validate" => validate(rules, Some(fixtures)),
        _ => Err("usage: mp_rule_validator capabilities | captures PATTERNS.json | validate RULES.yml [FIXTURES.json]".to_string()),
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(report) => {
            println!("{}", serde_json::to_string(&report).unwrap());
            if report.get("ok") == Some(&Value::Bool(true)) {
                ExitCode::SUCCESS
            } else {
                ExitCode::FAILURE
            }
        }
        Err(error) => {
            println!("{}", json!({"ok": false, "error": error}));
            ExitCode::FAILURE
        }
    }
}
