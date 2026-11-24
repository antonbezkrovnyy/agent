use std::collections::{BTreeMap, BTreeSet};
use std::net::IpAddr;
use std::sync::OnceLock;

use chrono::{DateTime, Offset};
use regex::Regex;
use serde_json::{Map, Value};

use crate::bundle::Taxonomy;

pub fn valid_hostname(value: &Value) -> bool {
    static HOSTNAME: OnceLock<Regex> = OnceLock::new();
    let Some(text) = value.as_str() else {
        return false;
    };
    if text.is_empty() || text.chars().any(char::is_whitespace) || text.contains('/') {
        return false;
    }
    if text.parse::<IpAddr>().is_ok() {
        return false;
    }
    HOSTNAME
        .get_or_init(|| Regex::new(r"\A[A-Za-z0-9_.\-]+\z").unwrap())
        .is_match(text)
}

fn datetime_utc(value: &Value) -> bool {
    value
        .as_str()
        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .is_some_and(|value| value.offset().fix().local_minus_utc() == 0)
}

pub fn validate_event(event: &Map<String, Value>, taxonomy: &Taxonomy) -> Vec<String> {
    let fields: BTreeMap<&str, _> = taxonomy
        .fields
        .iter()
        .map(|field| (field.name.as_str(), field))
        .collect();
    let mut issues = Vec::new();
    for field in &taxonomy.fields {
        if field.required
            && event
                .get(&field.name)
                .is_none_or(|value| value.is_null() || value.as_str() == Some(""))
        {
            issues.push(format!("missing_required_field:{}", field.name));
        }
    }
    let mut enum_values: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    for item in &taxonomy.values {
        enum_values
            .entry(&item.field)
            .or_default()
            .insert(&item.value);
    }
    for (name, value) in event {
        let Some(definition) = fields.get(name.as_str()) else {
            issues.push(format!("unknown_taxonomy_field:{name}"));
            continue;
        };
        if definition.automatic {
            issues.push(format!("automatic_field_forbidden:{name}"));
        }
        match definition.field_type.as_str() {
            "Number" if value.as_i64().is_none() && value.as_u64().is_none() => {
                issues.push(format!("invalid_number:{name}:{value}"));
            }
            "IPAddress" if value_to_string(value).parse::<IpAddr>().is_err() => {
                issues.push(format!("invalid_ip:{name}:{value}"));
            }
            "DateTime" if !datetime_utc(value) => {
                issues.push(format!("invalid_datetime_utc:{name}:{value}"));
            }
            "Enum"
                if !value.as_str().is_some_and(|value| {
                    enum_values
                        .get(name.as_str())
                        .is_some_and(|allowed| allowed.contains(value))
                }) =>
            {
                issues.push(format!("invalid_enum:{name}:{value}"));
            }
            "String" if !value.is_string() => {
                issues.push(format!("invalid_string:{name}:{value}"));
            }
            _ => {}
        }
    }
    for name in ["event_src.vendor", "event_src.title"] {
        if let Some(value) = event.get(name).and_then(Value::as_str) {
            if value != value.to_lowercase() || value.chars().any(char::is_whitespace) {
                issues.push(format!("invalid_source_name:{name}:{value}"));
            }
        }
    }
    if let Some(id) = event.get("id") {
        let valid = id.as_str().is_some_and(|id| {
            !id.is_empty() && !id.chars().any(char::is_whitespace) && id.split('_').count() >= 5
        });
        if !valid {
            issues.push(
                "invalid_id_format:expected=namespace_vendor_title_transport_event-type"
                    .to_string(),
            );
        }
    }
    if let Some(hostname) = event.get("event_src.hostname") {
        if !valid_hostname(hostname) {
            issues.push(format!("invalid_event_src_hostname:{hostname}"));
        }
    }
    let generic = event
        .get("category.generic")
        .and_then(Value::as_str)
        .unwrap_or("");
    let high = event
        .get("category.high")
        .and_then(Value::as_str)
        .unwrap_or("");
    let low = event
        .get("category.low")
        .and_then(Value::as_str)
        .unwrap_or("");
    if [!generic.is_empty(), !high.is_empty(), !low.is_empty()]
        .into_iter()
        .any(|value| value)
    {
        let valid = taxonomy.categories.iter().any(|category| {
            (generic.is_empty() || category.generic == generic)
                && (high.is_empty() || category.high == high)
                && (low.is_empty() || category.low == low)
        });
        if !valid {
            issues.push(format!("invalid_category_hierarchy:{generic}/{high}/{low}"));
        }
    }
    issues
}

fn value_to_string(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        _ => value.to_string(),
    }
}
