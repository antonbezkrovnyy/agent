use std::collections::BTreeSet;

use aho_corasick::AhoCorasick;
use anyhow::{Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::bundle::FamilyBundle;
use crate::mapping::apply_mappings;
use crate::normalization::normalize_captures;
use crate::parser::{Captures, CompiledParser};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessResult {
    pub ok: bool,
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub family_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parser_revision: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub normalization_revision: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mapping_revision: Option<u32>,
    pub captures: Captures,
    pub normalized: Map<String, Value>,
    pub event: Map<String, Value>,
    pub errors: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub candidates: Vec<String>,
}

impl ProcessResult {
    fn empty(status: &str) -> Self {
        Self {
            ok: false,
            status: status.to_string(),
            family_id: None,
            parser_revision: None,
            normalization_revision: None,
            mapping_revision: None,
            captures: Captures::new(),
            normalized: Map::new(),
            event: Map::new(),
            errors: Vec::new(),
            candidates: Vec::new(),
        }
    }
}

#[derive(Debug)]
pub struct FamilyEngine {
    pub bundle: FamilyBundle,
    parser: CompiledParser,
}

impl FamilyEngine {
    pub fn new(bundle: FamilyBundle) -> Result<Self> {
        let parser = CompiledParser::new(&bundle.parser)?;
        Ok(Self { bundle, parser })
    }

    pub fn parse_only(&self, raw: &str) -> std::result::Result<Captures, String> {
        self.parser.parse(raw)
    }

    pub fn process(&self, raw: &str) -> ProcessResult {
        match self.parser.parse(raw) {
            Ok(captures) => self.process_parsed(captures),
            Err(error) => {
                let mut result = self.base_result("parse_failed");
                result.errors.push(error);
                result
            }
        }
    }

    fn base_result(&self, status: &str) -> ProcessResult {
        ProcessResult {
            ok: false,
            status: status.to_string(),
            family_id: Some(self.bundle.family_id.clone()),
            parser_revision: Some(self.bundle.parser.revision),
            normalization_revision: Some(self.bundle.normalization.revision),
            mapping_revision: Some(self.bundle.mapping.revision),
            captures: Captures::new(),
            normalized: Map::new(),
            event: Map::new(),
            errors: Vec::new(),
            candidates: Vec::new(),
        }
    }

    pub fn process_parsed(&self, captures: Captures) -> ProcessResult {
        let mut result = self.base_result("normalization_failed");
        result.captures = captures;
        let normalized = normalize_captures(&result.captures, &self.bundle.normalization.fields);
        result.normalized = normalized.normalized;
        if !normalized.errors.is_empty() {
            result.errors = normalized.errors;
            return result;
        }
        let mapped = apply_mappings(
            &result.normalized,
            &self.bundle.mapping.mappings,
            self.bundle.mapping.source_timezone.as_deref(),
            &self.bundle.taxonomy,
        );
        result.event = mapped.event;
        result.errors = mapped.errors;
        result.ok = result.errors.is_empty();
        result.status = if result.ok {
            "mapped"
        } else {
            "mapping_failed"
        }
        .to_string();
        result
    }
}

#[derive(Debug)]
pub struct FamilyRegistry {
    engines: Vec<FamilyEngine>,
    matcher: Option<AhoCorasick>,
    family_anchors: Vec<BTreeSet<usize>>,
    fallback: BTreeSet<usize>,
}

impl FamilyRegistry {
    pub fn new(bundles: Vec<FamilyBundle>) -> Result<Self> {
        if bundles.is_empty() {
            bail!("empty_family_registry");
        }
        let mut engines: Vec<FamilyEngine> = bundles
            .into_iter()
            .map(FamilyEngine::new)
            .collect::<Result<_>>()?;
        engines.sort_by(|left, right| {
            left.bundle.family_id.cmp(&right.bundle.family_id).then(
                right
                    .bundle
                    .parser
                    .revision
                    .cmp(&left.bundle.parser.revision),
            )
        });
        let mut identities = BTreeSet::new();
        for engine in &engines {
            let identity = (
                engine.bundle.family_id.clone(),
                engine.bundle.parser.revision,
            );
            if !identities.insert(identity) {
                bail!("duplicate_family_revision");
            }
            if !engine.bundle.dispatch.fallback
                && engine.bundle.dispatch.strategy != "aho_corasick_all_anchors"
            {
                bail!(
                    "unsupported_dispatch_strategy:{}",
                    engine.bundle.dispatch.strategy
                );
            }
        }
        let anchors: BTreeSet<String> = engines
            .iter()
            .flat_map(|engine| engine.bundle.dispatch.anchors.clone())
            .collect();
        let anchors: Vec<String> = anchors.into_iter().collect();
        let matcher = if anchors.is_empty() {
            None
        } else {
            Some(AhoCorasick::new(&anchors)?)
        };
        let family_anchors = engines
            .iter()
            .map(|engine| {
                engine
                    .bundle
                    .dispatch
                    .anchors
                    .iter()
                    .map(|anchor| anchors.binary_search(anchor).unwrap())
                    .collect()
            })
            .collect();
        let fallback = engines
            .iter()
            .enumerate()
            .filter(|(_, engine)| engine.bundle.dispatch.fallback)
            .map(|(index, _)| index)
            .collect();
        Ok(Self {
            engines,
            matcher,
            family_anchors,
            fallback,
        })
    }

    pub fn candidates(&self, raw: &str) -> Vec<usize> {
        let mut candidates = self.fallback.clone();
        let mut matched_anchors = BTreeSet::new();
        if let Some(matcher) = &self.matcher {
            for matched in matcher.find_overlapping_iter(raw) {
                matched_anchors.insert(matched.pattern().as_usize());
            }
        }
        candidates.extend(
            self.family_anchors
                .iter()
                .enumerate()
                .filter(|(_, required)| {
                    !required.is_empty() && required.is_subset(&matched_anchors)
                })
                .map(|(index, _)| index),
        );
        candidates.into_iter().collect()
    }

    pub fn process(&self, raw: &str) -> ProcessResult {
        let candidates = self.candidates(raw);
        let candidate_names: Vec<String> = candidates
            .iter()
            .map(|index| self.family_label(*index))
            .collect();
        if candidates.is_empty() {
            let mut result = ProcessResult::empty("no_family");
            result.errors.push("no_dispatch_anchor_matched".to_string());
            return result;
        }
        let mut matched = Vec::new();
        let mut parse_errors = Vec::new();
        for index in candidates {
            match self.engines[index].parse_only(raw) {
                Ok(captures) => matched.push((index, captures)),
                Err(error) => parse_errors.push(format!("{}:{error}", self.family_label(index))),
            }
        }
        if matched.is_empty() {
            let mut result = ProcessResult::empty("no_family");
            result.candidates = candidate_names;
            result.errors = parse_errors;
            return result;
        }
        if matched.len() > 1 {
            let mut result = ProcessResult::empty("ambiguous_family");
            result.candidates = matched
                .iter()
                .map(|(index, _)| self.family_label(*index))
                .collect();
            result
                .errors
                .push("multiple_families_accepted_raw".to_string());
            return result;
        }
        let (index, captures) = matched.pop().unwrap();
        let mut result = self.engines[index].process_parsed(captures);
        result.candidates = candidate_names;
        result
    }

    fn family_label(&self, index: usize) -> String {
        let engine = &self.engines[index];
        format!(
            "{}@{}",
            engine.bundle.family_id, engine.bundle.parser.revision
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn process_result_serializes_empty_objects() {
        let result = ProcessResult::empty("no_family");
        let value = serde_json::to_value(result).unwrap();
        assert_eq!(value["captures"], serde_json::json!({}));
        assert_eq!(value["event"], serde_json::json!({}));
    }
}
