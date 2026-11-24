use std::collections::{BTreeMap, BTreeSet};
use std::net::IpAddr;
use std::sync::OnceLock;

use anyhow::{Context, Result, bail};
use fancy_regex::Regex;
use regex::Regex as LinearRegex;
use serde_json::Value;

use crate::bundle::{FieldConstraint, ParserFamily, RecordField};

pub type Captures = BTreeMap<String, String>;

#[derive(Debug)]
struct CompiledLayer {
    name: String,
    source: String,
    regex: Regex,
}

#[derive(Debug)]
enum CompiledCodec {
    Grok(Regex),
    Cef,
    Csv,
    Kv,
}

#[derive(Debug)]
struct CompiledStage {
    name: String,
    source: String,
    params: BTreeMap<String, Value>,
    codec: CompiledCodec,
}

#[derive(Debug)]
pub struct CompiledParser {
    layers: Vec<CompiledLayer>,
    stages: Vec<CompiledStage>,
    record_schema: Vec<RecordField>,
    constraints: Vec<FieldConstraint>,
}

impl CompiledParser {
    pub fn new(family: &ParserFamily) -> Result<Self> {
        if !family.layers.is_empty() && !family.stages.is_empty() {
            bail!("parser_modes_mixed");
        }
        if family.layers.is_empty() && family.stages.is_empty() {
            bail!("empty_parser");
        }
        let definitions: BTreeMap<String, String> = family
            .patterns
            .iter()
            .map(|pattern| (pattern.name.clone(), pattern.definition.clone()))
            .collect();
        let mut layers = Vec::new();
        for layer in &family.layers {
            layers.push(CompiledLayer {
                name: layer.name.clone(),
                source: layer.source.clone(),
                regex: compile_grok(&layer.pattern, &definitions)
                    .with_context(|| format!("invalid grok layer {}", layer.name))?,
            });
        }
        let mut stages = Vec::new();
        for stage in &family.stages {
            let codec = match stage.codec.as_str() {
                "grok" => {
                    let pattern = stage
                        .params
                        .get("pattern")
                        .and_then(Value::as_str)
                        .context("grok stage requires string params.pattern")?;
                    let stage_patterns = stage
                        .params
                        .get("patterns")
                        .and_then(Value::as_object)
                        .context("grok stage requires object params.patterns")?;
                    let mut definitions = BTreeMap::new();
                    for (name, definition) in stage_patterns {
                        definitions.insert(
                            name.clone(),
                            definition
                                .as_str()
                                .context("grok pattern definition must be string")?
                                .to_string(),
                        );
                    }
                    CompiledCodec::Grok(
                        compile_grok(pattern, &definitions)
                            .with_context(|| format!("invalid grok stage {}", stage.name))?,
                    )
                }
                "cef" => CompiledCodec::Cef,
                "csv" => CompiledCodec::Csv,
                "kv" => CompiledCodec::Kv,
                other => bail!("unsupported_codec:{other}"),
            };
            stages.push(CompiledStage {
                name: stage.name.clone(),
                source: stage.source.clone(),
                params: stage.params.clone(),
                codec,
            });
        }
        Ok(Self {
            layers,
            stages,
            record_schema: family.record_schema.clone(),
            constraints: family.constraints.clone(),
        })
    }

    pub fn parse(&self, raw: &str) -> std::result::Result<Captures, String> {
        let captures = if !self.layers.is_empty() {
            execute_layers(&self.layers, raw)?
        } else {
            execute_stages(&self.stages, raw)?
        };
        validate_captures(&captures, &self.record_schema, &self.constraints)?;
        Ok(captures)
    }
}

fn grok_reference() -> &'static LinearRegex {
    static REFERENCE: OnceLock<LinearRegex> = OnceLock::new();
    REFERENCE.get_or_init(|| {
        LinearRegex::new(r"%\{([A-Za-z0-9_]+)(?::([A-Za-z_][A-Za-z0-9_]*))?\}").unwrap()
    })
}

pub fn compile_grok(pattern: &str, patterns: &BTreeMap<String, String>) -> Result<Regex> {
    let mut expanded = pattern.to_string();
    for _ in 0..20 {
        let mut output = String::new();
        let mut last = 0;
        let mut changed = false;
        for captures in grok_reference().captures_iter(&expanded) {
            let whole = captures.get(0).unwrap();
            let name = captures.get(1).unwrap().as_str();
            let body = patterns
                .get(name)
                .with_context(|| format!("unknown_grok_pattern:{name}"))?;
            output.push_str(&expanded[last..whole.start()]);
            if let Some(capture) = captures.get(2) {
                output.push_str("(?P<");
                output.push_str(capture.as_str());
                output.push('>');
                output.push_str(body);
                output.push(')');
            } else {
                output.push_str("(?:");
                output.push_str(body);
                output.push(')');
            }
            last = whole.end();
            changed = true;
        }
        if !changed {
            let anchored = format!(r"\A(?:{expanded})\z");
            return Regex::new(&anchored).context("invalid_grok_regex");
        }
        output.push_str(&expanded[last..]);
        expanded = output;
    }
    bail!("grok_reference_depth_exceeded")
}

fn run_grok(regex: &Regex, value: &str) -> std::result::Result<Captures, String> {
    let captures = regex
        .captures(value)
        .map_err(|error| format!("grok_execution_error:{error}"))?
        .ok_or_else(|| "no_match: pattern must match the complete layer input".to_string())?;
    let mut result = Captures::new();
    for name in regex.capture_names().flatten() {
        if let Some(value) = captures.name(name) {
            result.insert(name.to_string(), value.as_str().to_string());
        }
    }
    Ok(result)
}

fn execute_layers(layers: &[CompiledLayer], raw: &str) -> std::result::Result<Captures, String> {
    let mut captures = Captures::new();
    let mut value = raw.to_string();
    for (index, layer) in layers.iter().enumerate() {
        if index == 0 && layer.source != "raw" {
            return Err(format!(
                "invalid_source:{}:first_layer_must_use_raw",
                layer.source
            ));
        }
        if index > 0 && !captures.contains_key(&layer.source) {
            return Err(format!("missing_source_capture:{}", layer.source));
        }
        let layer_captures = run_grok(&layer.regex, &value)
            .map_err(|error| format!("failed_layer:{}:{error}", layer.name))?;
        if let Some(next) = layers.get(index + 1) {
            value = layer_captures.get(&next.source).cloned().ok_or_else(|| {
                format!(
                    "missing_source_capture:{}:after={}",
                    next.source, layer.name
                )
            })?;
        }
        captures.extend(layer_captures);
    }
    Ok(captures)
}

fn execute_stages(stages: &[CompiledStage], raw: &str) -> std::result::Result<Captures, String> {
    let mut captures = Captures::new();
    let mut context = Captures::new();
    let mut names = BTreeSet::new();
    for (index, stage) in stages.iter().enumerate() {
        if !names.insert(&stage.name) {
            return Err(format!("duplicate_stage_name:{}", stage.name));
        }
        let input = if index == 0 {
            if stage.source != "raw" {
                return Err(format!(
                    "invalid_source:{}:first_stage_must_use_raw",
                    stage.source
                ));
            }
            raw
        } else {
            let prefix = format!("stages.{}.fields.", stages[index - 1].name);
            let field = stage.source.strip_prefix(&prefix).ok_or_else(|| {
                format!("invalid_source:{}:expected={prefix}<field>", stage.source)
            })?;
            if field.is_empty() || field.contains('.') {
                return Err(format!("invalid_source_field:{field}"));
            }
            context
                .get(field)
                .map(String::as_str)
                .ok_or_else(|| format!("missing_source_field:{}", stage.source))?
        };
        let fields = match &stage.codec {
            CompiledCodec::Grok(regex) => run_grok(regex, input),
            CompiledCodec::Cef => parse_cef(input, &stage.params),
            CompiledCodec::Csv => parse_csv(input, &stage.params),
            CompiledCodec::Kv => parse_kv(input, &stage.params),
        }
        .map_err(|error| format!("failed_stage:{}:{error}", stage.name))?;
        let collision: Vec<String> = captures
            .keys()
            .filter(|key| fields.contains_key(*key))
            .cloned()
            .collect();
        if !collision.is_empty() {
            return Err(format!(
                "stage_field_collision:{}:{collision:?}",
                stage.name
            ));
        }
        captures.extend(fields.clone());
        context = fields;
    }
    Ok(captures)
}

fn scalar_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        _ => value.to_string(),
    }
}

fn validate_captures(
    captures: &Captures,
    schema: &[RecordField],
    constraints: &[FieldConstraint],
) -> std::result::Result<(), String> {
    let mut issues = Vec::new();
    for field in schema {
        if field.name == "raw" || field.name == "residual" {
            continue;
        }
        let Some(value) = captures.get(&field.name) else {
            if field.required {
                issues.push(format!("missing_required_field:{}", field.name));
            }
            continue;
        };
        if field.required && value.is_empty() {
            issues.push(format!("missing_required_field:{}", field.name));
            continue;
        }
        let field_type = field.field_type.to_lowercase();
        let numeric = if field_type.contains("int") {
            match value.trim().parse::<i64>() {
                Ok(number) => Some(number),
                Err(_) => {
                    issues.push(format!("invalid_integer:{}:actual={value}", field.name));
                    None
                }
            }
        } else {
            None
        };
        if (field_type.starts_with("ip") || field_type.contains("ipv4"))
            && value.parse::<IpAddr>().is_err()
        {
            issues.push(format!("invalid_ip:{}:actual={value}", field.name));
        }
        if let Some(number) = numeric {
            if field.name == "pri" && !(0..=191).contains(&number) {
                issues.push(format!("invalid_pri_range:{}:actual={value}", field.name));
            }
            if (field.name == "src_port"
                || field.name == "dst_port"
                || field.name.ends_with("_port"))
                && !(0..=65535).contains(&number)
            {
                issues.push(format!("invalid_port_range:{}:actual={value}", field.name));
            }
        }
    }
    for constraint in constraints {
        let value = captures.get(&constraint.field);
        if let (Some(expected), Some(actual)) = (&constraint.equals, value) {
            if actual != &scalar_text(expected) {
                issues.push(format!(
                    "value_mismatch:{}:expected={}:actual={actual}",
                    constraint.field,
                    scalar_text(expected)
                ));
            }
        }
        if value.is_some() {
            for (condition_field, allowed) in &constraint.allowed_when {
                let actual = captures.get(condition_field);
                if !actual.is_some_and(|actual| allowed.contains(actual)) {
                    issues.push(format!(
                        "forbidden_field:{}:when={condition_field}={:?}",
                        constraint.field, actual
                    ));
                }
            }
        }
        if !constraint.required_when.is_empty()
            && constraint.required_when.iter().all(|(name, allowed)| {
                captures
                    .get(name)
                    .is_some_and(|actual| allowed.contains(actual))
            })
            && value.is_none_or(|value| value.is_empty())
        {
            issues.push(format!("missing_conditional_field:{}", constraint.field));
        }
    }
    if issues.is_empty() {
        Ok(())
    } else {
        Err(issues.join("; "))
    }
}

const CEF_HEADER_FIELDS: [&str; 6] = [
    "device_vendor",
    "device_product",
    "device_version",
    "signature_id",
    "name",
    "severity",
];

fn split_cef_header(raw: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut chars = raw.chars().peekable();
    while let Some(char) = chars.next() {
        if char == '\\' {
            current.push(char);
            if let Some(escaped) = chars.next() {
                current.push(escaped);
            }
        } else if char == '|' {
            parts.push(std::mem::take(&mut current));
        } else {
            current.push(char);
        }
    }
    parts.push(current);
    parts
}

fn cef_unescape(value: &str) -> String {
    let mut result = String::new();
    let mut chars = value.chars().peekable();
    while let Some(char) = chars.next() {
        if char == '\\' {
            if let Some(escaped) = chars.next() {
                match escaped {
                    'n' => result.push('\n'),
                    'r' => result.push('\r'),
                    '=' | '\\' | '|' | '"' => result.push(escaped),
                    _ => {
                        result.push('\\');
                        result.push(escaped);
                    }
                }
            } else {
                result.push(char);
            }
        } else {
            result.push(char);
        }
    }
    result
}

fn extension_key() -> &'static LinearRegex {
    static KEY: OnceLock<LinearRegex> = OnceLock::new();
    KEY.get_or_init(|| LinearRegex::new(r"^([A-Za-z][A-Za-z0-9_]*)=").unwrap())
}

fn parse_cef_extension(extension: &str) -> std::result::Result<Captures, String> {
    let mut fields = Captures::new();
    let mut position = 0;
    while position < extension.len() {
        while position < extension.len() && extension.as_bytes()[position].is_ascii_whitespace() {
            position += 1;
        }
        if position == extension.len() {
            break;
        }
        let key_match = extension_key()
            .captures(&extension[position..])
            .ok_or_else(|| format!("invalid_extension_key_at:{position}"))?;
        let whole = key_match.get(0).unwrap();
        let key = key_match.get(1).unwrap().as_str().to_string();
        if fields.contains_key(&key) {
            return Err(format!("duplicate_extension_key:{key}"));
        }
        position += whole.end();
        let value = if position < extension.len() && extension.as_bytes()[position] == b'"' {
            position += 1;
            let start = position;
            let mut end = None;
            while position < extension.len() {
                match extension.as_bytes()[position] {
                    b'\\' if position + 1 < extension.len() => position += 2,
                    b'"' => {
                        end = Some(position);
                        break;
                    }
                    _ => position += extension[position..].chars().next().unwrap().len_utf8(),
                }
            }
            let end = end.ok_or_else(|| format!("unterminated_quoted_value:{key}"))?;
            let value = &extension[start..end];
            position += 1;
            if position < extension.len() && !extension.as_bytes()[position].is_ascii_whitespace() {
                return Err(format!("invalid_character_after_quoted_value:{key}"));
            }
            value
        } else {
            let start = position;
            let mut value_end = extension.len();
            let mut next_key = extension.len();
            while position < extension.len() {
                if extension.as_bytes()[position] == b'\\' && position + 1 < extension.len() {
                    position += 2;
                    continue;
                }
                if extension.as_bytes()[position].is_ascii_whitespace() {
                    let boundary = position;
                    while position < extension.len()
                        && extension.as_bytes()[position].is_ascii_whitespace()
                    {
                        position += 1;
                    }
                    if extension_key().is_match(&extension[position..]) {
                        value_end = boundary;
                        next_key = position;
                        break;
                    }
                    continue;
                }
                position += extension[position..].chars().next().unwrap().len_utf8();
            }
            position = next_key;
            &extension[start..value_end]
        };
        fields.insert(key, cef_unescape(value));
    }
    Ok(fields)
}

fn parse_cef(raw: &str, params: &BTreeMap<String, Value>) -> std::result::Result<Captures, String> {
    if !params.is_empty() {
        return Err(format!(
            "unsupported_params:{:?}",
            params.keys().collect::<Vec<_>>()
        ));
    }
    if !raw.starts_with("CEF:") {
        return Err("invalid_cef_prefix".to_string());
    }
    let parts = split_cef_header(raw);
    if parts.len() != 8 {
        return Err(format!(
            "invalid_cef_header:expected=7:actual={}",
            parts.len() - 1
        ));
    }
    let version = cef_unescape(&parts[0][4..]);
    if version.is_empty() {
        return Err("invalid_cef_version".to_string());
    }
    let mut fields = Captures::new();
    fields.insert("cef_version".to_string(), version);
    for (name, value) in CEF_HEADER_FIELDS.iter().zip(&parts[1..7]) {
        fields.insert((*name).to_string(), cef_unescape(value));
    }
    let extension = parse_cef_extension(&parts[7])?;
    for (key, value) in extension {
        if fields.contains_key(&key) {
            return Err(format!("cef_field_collision:{key}"));
        }
        fields.insert(key, value);
    }
    Ok(fields)
}

fn check_params(
    params: &BTreeMap<String, Value>,
    allowed: &[&str],
) -> std::result::Result<(), String> {
    let allowed: BTreeSet<&str> = allowed.iter().copied().collect();
    let unknown: Vec<&str> = params
        .keys()
        .map(String::as_str)
        .filter(|name| !allowed.contains(name))
        .collect();
    if unknown.is_empty() {
        Ok(())
    } else {
        Err(format!("unsupported_params:{unknown:?}"))
    }
}

fn char_param(
    params: &BTreeMap<String, Value>,
    name: &str,
    default: Option<char>,
) -> std::result::Result<Option<char>, String> {
    match params.get(name) {
        None => Ok(default),
        Some(Value::Null) => Ok(None),
        Some(Value::String(value)) if value.chars().count() == 1 => Ok(value.chars().next()),
        _ => Err(format!(
            "invalid_param:{name}:expected_one_character_or_null"
        )),
    }
}

fn string_param<'a>(
    params: &'a BTreeMap<String, Value>,
    name: &str,
    default: Option<&'a str>,
) -> std::result::Result<Option<&'a str>, String> {
    match params.get(name) {
        None => Ok(default),
        Some(Value::Null) => Ok(None),
        Some(Value::String(value)) if !value.is_empty() => Ok(Some(value)),
        _ => Err(format!(
            "invalid_param:{name}:expected_nonempty_string_or_null"
        )),
    }
}

fn bool_param(
    params: &BTreeMap<String, Value>,
    name: &str,
    default: bool,
) -> std::result::Result<bool, String> {
    match params.get(name) {
        None => Ok(default),
        Some(Value::Bool(value)) => Ok(*value),
        _ => Err(format!("invalid_param:{name}:expected_boolean")),
    }
}

fn usize_param(
    params: &BTreeMap<String, Value>,
    name: &str,
) -> std::result::Result<Option<usize>, String> {
    match params.get(name) {
        None => Ok(None),
        Some(Value::Number(value)) => value
            .as_u64()
            .filter(|value| *value >= 1)
            .map(|value| Some(value as usize))
            .ok_or_else(|| format!("invalid_param:{name}:expected_integer>=1")),
        _ => Err(format!("invalid_param:{name}:expected_integer>=1")),
    }
}

fn read_delimited_row(
    raw: &str,
    delimiter: char,
    quotechar: Option<char>,
    escapechar: Option<char>,
    doublequote: bool,
    skipinitialspace: bool,
    strict: bool,
) -> std::result::Result<Vec<String>, String> {
    let mut row = Vec::new();
    let mut field = String::new();
    let mut chars = raw.chars().peekable();
    let mut quoted = false;
    let mut closed_quote = false;
    let mut field_start = true;
    while let Some(char) = chars.next() {
        if char == '\n' || char == '\r' {
            return Err("expected_one_record".to_string());
        }
        if quoted {
            if Some(char) == escapechar {
                field.push(
                    chars
                        .next()
                        .ok_or_else(|| "escape_at_end_of_input".to_string())?,
                );
            } else if Some(char) == quotechar {
                if doublequote && chars.peek() == quotechar.as_ref() {
                    field.push(char);
                    chars.next();
                } else {
                    quoted = false;
                    closed_quote = true;
                }
            } else {
                field.push(char);
            }
            continue;
        }
        if char == delimiter {
            row.push(std::mem::take(&mut field));
            field_start = true;
            closed_quote = false;
        } else if closed_quote {
            if strict {
                return Err("character_after_closing_quote".to_string());
            }
            field.push(char);
            closed_quote = false;
        } else if field_start && skipinitialspace && char == ' ' {
            continue;
        } else if field_start && Some(char) == quotechar {
            quoted = true;
            field_start = false;
        } else if Some(char) == escapechar {
            field.push(
                chars
                    .next()
                    .ok_or_else(|| "escape_at_end_of_input".to_string())?,
            );
            field_start = false;
        } else {
            field.push(char);
            field_start = false;
        }
    }
    if quoted {
        return Err("unexpected_end_inside_quoted_field".to_string());
    }
    row.push(field);
    Ok(row)
}

fn positional_names(
    params: &BTreeMap<String, Value>,
) -> std::result::Result<BTreeMap<usize, String>, String> {
    let mut result = BTreeMap::new();
    if let Some(value) = params.get("positional_names") {
        let object = value
            .as_object()
            .ok_or_else(|| "invalid_param:positional_names".to_string())?;
        for (index, name) in object {
            let index = index
                .parse::<usize>()
                .ok()
                .filter(|index| *index >= 1)
                .ok_or_else(|| "invalid_param:positional_names:index".to_string())?;
            let name = name
                .as_str()
                .filter(|name| !name.is_empty())
                .ok_or_else(|| "invalid_param:positional_names:name".to_string())?;
            result.insert(index, name.to_string());
        }
    }
    Ok(result)
}

fn field_names(params: &BTreeMap<String, Value>) -> std::result::Result<Vec<String>, String> {
    match params.get("field_names") {
        None => Ok(Vec::new()),
        Some(Value::Array(values)) => values
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .filter(|value| !value.is_empty())
                    .map(str::to_string)
                    .ok_or_else(|| "invalid_param:field_names".to_string())
            })
            .collect(),
        _ => Err("invalid_param:field_names".to_string()),
    }
}

fn duplicate_policy(params: &BTreeMap<String, Value>) -> std::result::Result<String, String> {
    match params.get("duplicate_policy") {
        None => Ok("error".to_string()),
        Some(Value::String(value)) if ["error", "first", "last"].contains(&value.as_str()) => {
            Ok(value.clone())
        }
        _ => Err("invalid_param:duplicate_policy:expected=error|first|last".to_string()),
    }
}

fn assign_field(
    fields: &mut Captures,
    key: String,
    value: String,
    policy: &str,
) -> std::result::Result<(), String> {
    if fields.contains_key(&key) {
        match policy {
            "error" => return Err(format!("duplicate_field:{key}")),
            "first" => return Ok(()),
            _ => {}
        }
    }
    fields.insert(key, value);
    Ok(())
}

fn delimited_fields(
    raw: &str,
    params: &BTreeMap<String, Value>,
    csv: bool,
) -> std::result::Result<Captures, String> {
    const CSV_PARAMS: &[&str] = &[
        "delimiter",
        "quotechar",
        "escapechar",
        "doublequote",
        "skipinitialspace",
        "strict",
        "label_separator",
        "positional_prefix",
        "positional_names",
        "field_names",
        "strip_keys",
        "strip_values",
        "allow_positionals",
        "allow_empty_keys",
        "allow_empty_fields",
        "expected_columns",
        "duplicate_policy",
    ];
    const KV_PARAMS: &[&str] = &[
        "pair_separator",
        "key_value_separator",
        "quotechar",
        "escapechar",
        "doublequote",
        "skipinitialspace",
        "strict",
        "positional_prefix",
        "positional_names",
        "strip_keys",
        "strip_values",
        "allow_positionals",
        "allow_empty_keys",
        "allow_empty_values",
        "expected_pairs",
        "duplicate_policy",
    ];
    check_params(params, if csv { CSV_PARAMS } else { KV_PARAMS })?;
    if raw.is_empty() {
        return Err(if csv {
            "empty_csv_record"
        } else {
            "empty_kv_record"
        }
        .to_string());
    }
    let delimiter_name = if csv { "delimiter" } else { "pair_separator" };
    let delimiter = char_param(params, delimiter_name, Some(','))?
        .ok_or_else(|| format!("invalid_param:{delimiter_name}"))?;
    let quotechar = char_param(params, "quotechar", Some('"'))?;
    let escapechar = char_param(params, "escapechar", None)?;
    let doublequote = bool_param(params, "doublequote", true)?;
    let skipinitialspace = bool_param(params, "skipinitialspace", false)?;
    let strict = bool_param(params, "strict", true)?;
    let strip_keys = bool_param(params, "strip_keys", true)?;
    let strip_values = bool_param(params, "strip_values", false)?;
    let allow_positionals = bool_param(params, "allow_positionals", csv)?;
    let allow_empty_keys = bool_param(params, "allow_empty_keys", false)?;
    let allow_empty_values = bool_param(
        params,
        if csv {
            "allow_empty_fields"
        } else {
            "allow_empty_values"
        },
        true,
    )?;
    let expected = usize_param(
        params,
        if csv {
            "expected_columns"
        } else {
            "expected_pairs"
        },
    )?;
    let policy = duplicate_policy(params)?;
    let positional_names = positional_names(params)?;
    let names = field_names(params)?;
    let prefix = string_param(params, "positional_prefix", Some("column_"))?.unwrap_or("column_");
    let label_separator = if csv {
        string_param(params, "label_separator", None)?
    } else {
        None
    };
    let key_value_separator = if csv {
        None
    } else {
        string_param(params, "key_value_separator", Some("="))?
    };
    let row = read_delimited_row(
        raw,
        delimiter,
        quotechar,
        escapechar,
        doublequote,
        skipinitialspace,
        strict,
    )?;
    if expected.is_some_and(|expected| expected != row.len()) {
        return Err(format!(
            "unexpected_field_count:expected={}:actual={}",
            expected.unwrap(),
            row.len()
        ));
    }
    if csv && !names.is_empty() && names.len() != row.len() {
        return Err(format!(
            "field_names_mismatch:expected={}:actual={}",
            names.len(),
            row.len()
        ));
    }
    let mut fields = Captures::new();
    for (offset, cell) in row.iter().enumerate() {
        let index = offset + 1;
        let separator = if csv {
            label_separator
        } else {
            key_value_separator
        };
        let labeled = separator.and_then(|separator| cell.split_once(separator));
        if labeled.is_none() && !allow_positionals && (!csv || separator.is_some()) {
            return Err(format!("missing_field_label:column={index}"));
        }
        let (mut key, mut value) = if let Some((key, value)) = labeled {
            (key.to_string(), value.to_string())
        } else {
            let key = positional_names
                .get(&index)
                .cloned()
                .or_else(|| names.get(offset).cloned())
                .unwrap_or_else(|| format!("{prefix}{index}"));
            (key, cell.clone())
        };
        if strip_keys {
            key = key.trim().to_string();
        }
        if strip_values {
            value = value.trim().to_string();
        }
        if key.is_empty() && !allow_empty_keys {
            return Err(format!("empty_key:column={index}"));
        }
        if value.is_empty() && !allow_empty_values {
            return Err(format!("empty_value:{key}"));
        }
        assign_field(&mut fields, key, value, &policy)?;
    }
    Ok(fields)
}

fn parse_csv(raw: &str, params: &BTreeMap<String, Value>) -> std::result::Result<Captures, String> {
    delimited_fields(raw, params, true)
}

fn parse_kv(raw: &str, params: &BTreeMap<String, Value>) -> std::result::Result<Captures, String> {
    delimited_fields(raw, params, false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grok_supports_lookahead_and_named_captures() {
        let patterns = BTreeMap::from([
            (
                "HOST".to_string(),
                r"(?=[A-Za-z0-9_.\-]*[A-Za-z])[A-Za-z0-9_.\-]+".to_string(),
            ),
            ("INT".to_string(), r"[0-9]+".to_string()),
        ]);
        let regex = compile_grok(r"%{HOST:host}:%{INT:port}", &patterns).unwrap();
        let captures = run_grok(&regex, "asa-01:443").unwrap();
        assert_eq!(captures.get("host").unwrap(), "asa-01");
        assert_eq!(captures.get("port").unwrap(), "443");
        assert!(run_grok(&regex, "10.0.0.1:443").is_err());
    }

    #[test]
    fn cef_parses_quoted_extension_and_escapes() {
        let fields = parse_cef(
            r#"CEF:0|Vendor|Product|1.0|100|Name|5|src=10.0.0.1 msg="hello world" dst=10.0.0.2"#,
            &BTreeMap::new(),
        )
        .unwrap();
        assert_eq!(fields.get("src").unwrap(), "10.0.0.1");
        assert_eq!(fields.get("msg").unwrap(), "hello world");
        assert_eq!(fields.get("dst").unwrap(), "10.0.0.2");
    }

    #[test]
    fn kv_supports_custom_separators_and_quotes() {
        let params = BTreeMap::from([
            ("pair_separator".to_string(), Value::String(",".to_string())),
            (
                "key_value_separator".to_string(),
                Value::String(":".to_string()),
            ),
            ("allow_positionals".to_string(), Value::Bool(true)),
            ("strip_values".to_string(), Value::Bool(true)),
        ]);
        let fields = parse_kv(
            r#"IP,Local Port: 2,Remote Host Name: ,Action: Allowed"#,
            &params,
        )
        .unwrap();
        assert_eq!(fields.get("Local Port").unwrap(), "2");
        assert_eq!(fields.get("column_1").unwrap(), "IP");
        assert_eq!(fields.get("Action").unwrap(), "Allowed");
    }
}
