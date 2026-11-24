use std::collections::{BTreeMap, BTreeSet};
use std::net::IpAddr;

use chrono::{DateTime, FixedOffset, NaiveDateTime, SecondsFormat, TimeZone};
use chrono_tz::Tz;
use serde_json::{Map, Value};

use crate::bundle::{NormalizationField, TransformOperation};
use crate::parser::Captures;

#[derive(Debug)]
pub struct NormalizationOutput {
    pub normalized: Map<String, Value>,
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Copy)]
pub(crate) enum ParsedTimezone {
    Fixed(FixedOffset),
    Named(Tz),
}

pub(crate) fn parse_timezone(value: &str) -> Result<ParsedTimezone, String> {
    if matches!(value, "UTC" | "Z" | "+00:00" | "-00:00") {
        return Ok(ParsedTimezone::Fixed(FixedOffset::east_opt(0).unwrap()));
    }
    if value.len() == 6 && matches!(&value[..1], "+" | "-") {
        let hours = value[1..3]
            .parse::<i32>()
            .map_err(|_| format!("invalid_timezone:{value}"))?;
        let minutes = value[4..6]
            .parse::<i32>()
            .map_err(|_| format!("invalid_timezone:{value}"))?;
        if hours > 23 || minutes > 59 || &value[3..4] != ":" {
            return Err(format!("invalid_timezone:{value}"));
        }
        let seconds = (hours * 3600 + minutes * 60) * if &value[..1] == "-" { -1 } else { 1 };
        return FixedOffset::east_opt(seconds)
            .map(ParsedTimezone::Fixed)
            .ok_or_else(|| format!("invalid_timezone:{value}"));
    }
    value
        .parse::<Tz>()
        .map(ParsedTimezone::Named)
        .map_err(|_| format!("invalid_timezone:{value}"))
}

fn localize(
    timezone: ParsedTimezone,
    value: NaiveDateTime,
) -> Result<DateTime<FixedOffset>, String> {
    match timezone {
        ParsedTimezone::Fixed(offset) => offset
            .from_local_datetime(&value)
            .single()
            .ok_or_else(|| "invalid_local_datetime".to_string()),
        ParsedTimezone::Named(timezone) => timezone
            .from_local_datetime(&value)
            .single()
            .map(|value| value.fixed_offset())
            .ok_or_else(|| "ambiguous_or_nonexistent_local_datetime".to_string()),
    }
}

fn convert_timezone(
    timezone: ParsedTimezone,
    value: DateTime<FixedOffset>,
) -> DateTime<FixedOffset> {
    match timezone {
        ParsedTimezone::Fixed(offset) => value.with_timezone(&offset),
        ParsedTimezone::Named(timezone) => value.with_timezone(&timezone).fixed_offset(),
    }
}

fn format_naive(value: NaiveDateTime) -> String {
    value.format("%Y-%m-%dT%H:%M:%S%.f").to_string()
}

fn timestamp_value(value: &Value, params: &BTreeMap<String, Value>) -> Result<Value, String> {
    let formats: Vec<&str> = match (params.get("formats"), params.get("format")) {
        (Some(Value::Array(formats)), _) if !formats.is_empty() => formats
            .iter()
            .map(|format| {
                format
                    .as_str()
                    .ok_or_else(|| "parse_timestamp: formats must contain strings".to_string())
            })
            .collect::<Result<_, _>>()?,
        (None, Some(Value::String(format))) => vec![format],
        _ => return Err("parse_timestamp: requires nonempty formats or format".to_string()),
    };
    let text = match value {
        Value::String(text) => text.clone(),
        _ => value.to_string(),
    };
    let input_timezone = match params.get("input_timezone") {
        None => None,
        Some(Value::String(value)) => Some(parse_timezone(value)?),
        _ => return Err("parse_timestamp: input_timezone must be string".to_string()),
    };
    let output_timezone = match params.get("output_timezone") {
        None => None,
        Some(Value::String(value)) => Some(parse_timezone(value)?),
        _ => return Err("parse_timestamp: output_timezone must be string".to_string()),
    };
    let mut last_error = "unknown".to_string();
    for format in formats {
        let missing_year = !format.contains("%Y") && !format.contains("%y");
        let (parse_text, parse_format) = if missing_year {
            let year = params
                .get("default_year")
                .and_then(Value::as_i64)
                .filter(|year| (1..=9999).contains(year))
                .ok_or_else(|| {
                    "parse_timestamp: valid default_year is required when format has no year"
                        .to_string()
                })?;
            (format!("{year} {text}"), format!("%Y {format}"))
        } else {
            (text.clone(), format.to_string())
        };
        let aware = DateTime::parse_from_str(&parse_text, &parse_format);
        let naive = NaiveDateTime::parse_from_str(&parse_text, &parse_format);
        if aware.is_err() && naive.is_err() {
            last_error = naive.err().or_else(|| aware.err()).unwrap().to_string();
            continue;
        }
        if let Ok(value) = aware {
            return finish_timestamp(value, input_timezone, output_timezone);
        }
        return finish_naive_timestamp(naive.unwrap(), input_timezone, output_timezone);
    }
    Err(format!(
        "parse_timestamp: format did not match: {last_error}"
    ))
}

fn finish_timestamp(
    value: DateTime<FixedOffset>,
    input_timezone: Option<ParsedTimezone>,
    output_timezone: Option<ParsedTimezone>,
) -> Result<Value, String> {
    let value = if let Some(input) = input_timezone {
        localize(input, value.naive_local())?
    } else {
        value
    };
    let value = if let Some(output) = output_timezone {
        convert_timezone(output, value)
    } else {
        value
    };
    Ok(Value::String(
        value.to_rfc3339_opts(SecondsFormat::AutoSi, false),
    ))
}

fn finish_naive_timestamp(
    value: NaiveDateTime,
    input_timezone: Option<ParsedTimezone>,
    output_timezone: Option<ParsedTimezone>,
) -> Result<Value, String> {
    match (input_timezone, output_timezone) {
        (None, Some(_)) => {
            Err("parse_timestamp: output_timezone requires input_timezone".to_string())
        }
        (Some(input), output) => {
            let value = localize(input, value)?;
            let value = output.map_or(value, |output| convert_timezone(output, value));
            Ok(Value::String(
                value.to_rfc3339_opts(SecondsFormat::AutoSi, false),
            ))
        }
        (None, None) => Ok(Value::String(format_naive(value))),
    }
}

fn allowed_params(operation: &str) -> &'static [&'static str] {
    match operation {
        "trim" | "to_integer" | "lowercase" => &[],
        "null_if" => &["values"],
        "normalize_ip" => &["version"],
        "validate_range" => &["min", "max"],
        "parse_timestamp" => &[
            "format",
            "formats",
            "default_year",
            "input_timezone",
            "output_timezone",
        ],
        _ => &[],
    }
}

pub fn apply_transform(operation: &TransformOperation, value: &Value) -> Result<Value, String> {
    let allowed: BTreeSet<&str> = allowed_params(&operation.op).iter().copied().collect();
    if ![
        "trim",
        "null_if",
        "to_integer",
        "lowercase",
        "normalize_ip",
        "validate_range",
        "parse_timestamp",
    ]
    .contains(&operation.op.as_str())
    {
        return Err(format!("unsupported_operation:{}", operation.op));
    }
    let unknown: Vec<&str> = operation
        .params
        .keys()
        .map(String::as_str)
        .filter(|name| !allowed.contains(name))
        .collect();
    if !unknown.is_empty() {
        return Err(format!("invalid_params:{}:{unknown:?}", operation.op));
    }
    if value.is_null() {
        return Ok(Value::Null);
    }
    match operation.op.as_str() {
        "trim" => Ok(Value::String(value_to_string(value).trim().to_string())),
        "null_if" => {
            let values = operation
                .params
                .get("values")
                .and_then(Value::as_array)
                .ok_or_else(|| "null_if: values must be a list".to_string())?;
            Ok(if values.contains(value) {
                Value::Null
            } else {
                value.clone()
            })
        }
        "to_integer" => match value {
            Value::Bool(_) => Err("to_integer: boolean is not integer".to_string()),
            Value::Number(number) => number
                .as_i64()
                .map(Value::from)
                .or_else(|| number.as_u64().map(Value::from))
                .or_else(|| number.as_f64().map(|value| Value::from(value as i64)))
                .ok_or_else(|| "to_integer: invalid number".to_string()),
            Value::String(text) => text
                .trim()
                .parse::<i64>()
                .map(Value::from)
                .map_err(|_| format!("to_integer: invalid value: {text}")),
            _ => Err("to_integer: expected string or number".to_string()),
        },
        "lowercase" => Ok(Value::String(value_to_string(value).to_lowercase())),
        "normalize_ip" => {
            let version = operation
                .params
                .get("version")
                .and_then(Value::as_str)
                .unwrap_or("any");
            if !["any", "ipv4", "ipv6"].contains(&version) {
                return Err("normalize_ip: version must be any, ipv4 or ipv6".to_string());
            }
            let address = value_to_string(value)
                .parse::<IpAddr>()
                .map_err(|_| "normalize_ip: invalid IP address".to_string())?;
            let actual = if address.is_ipv4() { "ipv4" } else { "ipv6" };
            if version != "any" && version != actual {
                return Err(format!("normalize_ip: expected {version}, got {actual}"));
            }
            Ok(Value::String(address.to_string()))
        }
        "validate_range" => {
            let number = value
                .as_f64()
                .filter(|_| !value.is_boolean())
                .ok_or_else(|| "validate_range: apply numeric transform first".to_string())?;
            if let Some(minimum) = operation.params.get("min").and_then(Value::as_f64) {
                if number < minimum {
                    return Err(format!("validate_range: {number} < {minimum}"));
                }
            }
            if let Some(maximum) = operation.params.get("max").and_then(Value::as_f64) {
                if number > maximum {
                    return Err(format!("validate_range: {number} > {maximum}"));
                }
            }
            Ok(value.clone())
        }
        "parse_timestamp" => timestamp_value(value, &operation.params),
        _ => unreachable!(),
    }
}

fn value_to_string(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        _ => value.to_string(),
    }
}

pub fn normalize_captures(
    captures: &Captures,
    fields: &[NormalizationField],
) -> NormalizationOutput {
    let mut normalized = Map::new();
    for (name, value) in captures {
        normalized.insert(name.clone(), Value::String(value.clone()));
    }
    let mut errors = Vec::new();
    for field in fields {
        let Some(capture) = captures.get(&field.source) else {
            if field.required {
                errors.push(format!("missing_required_source:{}", field.source));
            }
            continue;
        };
        let mut value = Value::String(capture.clone());
        let mut failed = false;
        for operation in &field.operations {
            match apply_transform(operation, &value) {
                Ok(result) => value = result,
                Err(error) => {
                    errors.push(format!("{}:{}:{error}", field.source, operation.op));
                    failed = true;
                    break;
                }
            }
        }
        if failed {
            continue;
        }
        if field.required && (value.is_null() || value.as_str() == Some("")) {
            errors.push(format!("missing_required_after_transform:{}", field.source));
        }
        normalized.insert(field.source.clone(), value);
    }
    NormalizationOutput { normalized, errors }
}

pub(crate) fn utc_datetime(value: &Value, source_timezone: Option<&str>) -> Result<String, String> {
    let text = value_to_string(value);
    if let Ok(value) = DateTime::parse_from_rfc3339(&text) {
        return Ok(value
            .with_timezone(&FixedOffset::east_opt(0).unwrap())
            .to_rfc3339_opts(SecondsFormat::AutoSi, true));
    }
    let normalized_text = text.replacen(' ', "T", 1);
    let naive = NaiveDateTime::parse_from_str(&normalized_text, "%Y-%m-%dT%H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(&normalized_text, "%Y-%m-%d"))
        .map_err(|_| format!("invalid_iso_datetime:{text}"))?;
    let source =
        source_timezone.ok_or_else(|| "naive_datetime_requires_source_timezone".to_string())?;
    let value = localize(parse_timezone(source)?, naive)?;
    Ok(value
        .with_timezone(&FixedOffset::east_opt(0).unwrap())
        .to_rfc3339_opts(SecondsFormat::AutoSi, true))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn operation(name: &str, params: BTreeMap<String, Value>) -> TransformOperation {
        TransformOperation {
            op: name.to_string(),
            params,
        }
    }

    #[test]
    fn cisco_timestamp_stays_naive_without_timezone() {
        let params = BTreeMap::from([(
            "format".to_string(),
            Value::String("%b %d %Y %H:%M:%S".to_string()),
        )]);
        let result = apply_transform(
            &operation("parse_timestamp", params),
            &Value::String("Feb 12 2023 13:00:30".to_string()),
        )
        .unwrap();
        assert_eq!(result, Value::String("2023-02-12T13:00:30".to_string()));
    }

    #[test]
    fn timestamp_without_year_requires_and_applies_default_year() {
        let params = BTreeMap::from([
            (
                "format".to_string(),
                Value::String("%b %d %H:%M:%S".to_string()),
            ),
            ("default_year".to_string(), Value::from(2023)),
            (
                "input_timezone".to_string(),
                Value::String("UTC".to_string()),
            ),
            (
                "output_timezone".to_string(),
                Value::String("UTC".to_string()),
            ),
        ]);
        let result = apply_transform(
            &operation("parse_timestamp", params),
            &Value::String("Feb 12 13:00:30".to_string()),
        )
        .unwrap();
        assert_eq!(
            result,
            Value::String("2023-02-12T13:00:30+00:00".to_string())
        );
    }

    #[test]
    fn mapping_timestamp_uses_explicit_source_timezone() {
        let result = utc_datetime(
            &Value::String("2023-02-12T13:00:30".to_string()),
            Some("UTC"),
        )
        .unwrap();
        assert_eq!(result, "2023-02-12T13:00:30Z");
        assert!(utc_datetime(&Value::String("2023-02-12T13:00:30".to_string()), None).is_err());
    }
}
