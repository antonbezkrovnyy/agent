use std::collections::{BTreeMap, BTreeSet};
use std::net::IpAddr;

use serde_json::{Map, Value};

use crate::bundle::{MappingField, Taxonomy};
use crate::normalization::utc_datetime;
use crate::taxonomy::{valid_hostname, validate_event};

#[derive(Debug)]
pub struct MappingOutput {
    pub event: Map<String, Value>,
    pub errors: Vec<String>,
}

pub fn mapping_rank(target: &str) -> u32 {
    if [
        "time",
        "id",
        "event_src.vendor",
        "event_src.title",
        "event_src.category",
        "action",
        "object",
        "status",
        "importance",
    ]
    .contains(&target)
    {
        0
    } else if target.starts_with("category.") {
        20
    } else if target == "subject" || target.starts_with("subject.") {
        30
    } else if target.starts_with("object.") {
        40
    } else if target.starts_with("src.")
        || target.starts_with("dst.")
        || target.starts_with("assigned_")
    {
        50
    } else if target.starts_with("event_src.") {
        60
    } else if [
        "protocol",
        "protocol.layer7",
        "direction",
        "duration",
        "start_time",
        "chain_id",
        "logon_auth_method",
        "logon_service",
        "logon_type",
        "reason",
    ]
    .contains(&target)
    {
        70
    } else {
        80
    }
}

fn condition_matches(
    value: &Value,
    condition: Option<&BTreeMap<String, String>>,
) -> Result<bool, String> {
    let Some(condition) = condition else {
        return Ok(true);
    };
    if condition.len() != 1 || !condition.contains_key("format") {
        return Err("invalid_condition:expected={format: ip|ipv4|ipv6|hostname}".to_string());
    }
    let format = &condition["format"];
    let text = match value {
        Value::String(text) => text.clone(),
        _ => value.to_string(),
    };
    match format.as_str() {
        "ip" | "ipv4" | "ipv6" => {
            let Ok(address) = text.parse::<IpAddr>() else {
                return Ok(false);
            };
            Ok(match format.as_str() {
                "ip" => true,
                "ipv4" => address.is_ipv4(),
                _ => address.is_ipv6(),
            })
        }
        "hostname" => Ok(valid_hostname(value)),
        _ => Err(format!("invalid_condition_format:{format}")),
    }
}

pub fn apply_mappings(
    normalized: &Map<String, Value>,
    mappings: &[MappingField],
    source_timezone: Option<&str>,
    taxonomy: &Taxonomy,
) -> MappingOutput {
    let mut event = Map::new();
    let mut errors = Vec::new();
    let mut seen = BTreeSet::new();
    let duplicates: BTreeSet<&str> = mappings
        .iter()
        .map(|mapping| mapping.target.as_str())
        .filter(|target| !seen.insert(*target))
        .collect();
    if !duplicates.is_empty() {
        errors.push(format!("duplicate_mapping_targets:{duplicates:?}"));
    }
    let mut previous_rank = 0;
    for mapping in mappings {
        let rank = mapping_rank(&mapping.target);
        if rank < previous_rank {
            errors.push(format!(
                "mapping_order_violation:{}:rank={}:previous_rank={}",
                mapping.target, rank, previous_rank
            ));
        }
        previous_rank = previous_rank.max(rank);
        let mut value = if let Some(source) = &mapping.source {
            let Some(value) = normalized.get(source) else {
                continue;
            };
            match condition_matches(value, mapping.condition.as_ref()) {
                Ok(true) => {}
                Ok(false) => continue,
                Err(error) => {
                    errors.push(format!("{source}->{}:{error}", mapping.target));
                    continue;
                }
            }
            if let Some(value_map) = &mapping.value_map {
                let key = match value {
                    Value::String(value) => value.clone(),
                    _ => value.to_string(),
                };
                let Some(mapped) = value_map.get(&key) else {
                    errors.push(format!("unmapped_source_value:{source}:{key}"));
                    continue;
                };
                mapped.clone()
            } else {
                value.clone()
            }
        } else if let Some(constant) = &mapping.constant {
            constant.clone()
        } else {
            errors.push(format!(
                "mapping_without_source_or_constant:{}",
                mapping.target
            ));
            continue;
        };
        if ["time", "start_time"].contains(&mapping.target.as_str()) {
            match utc_datetime(&value, source_timezone) {
                Ok(converted) => value = Value::String(converted),
                Err(error) => {
                    errors.push(format!("{}:{error}", mapping.target));
                    continue;
                }
            }
        }
        event.insert(mapping.target.clone(), value);
    }
    errors.extend(validate_event(&event, taxonomy));
    MappingOutput { event, errors }
}
