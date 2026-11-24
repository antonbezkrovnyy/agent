use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FamilyBundle {
    pub schema_version: u32,
    pub family_id: String,
    pub dispatch: Dispatch,
    pub parser: ParserFamily,
    pub normalization: NormalizationRevision,
    pub mapping: MappingRevision,
    pub taxonomy: Taxonomy,
    pub compatibility_hash: String,
}

impl FamilyBundle {
    pub fn from_path(path: &Path) -> Result<Self> {
        let text = fs::read_to_string(path)
            .with_context(|| format!("cannot read bundle {}", path.display()))?;
        Self::from_json(&text)
    }

    pub fn from_json(text: &str) -> Result<Self> {
        let value: Value = serde_json::from_str(text).context("invalid bundle JSON")?;
        let actual_hash = calculate_compatibility_hash(&value)?;
        let bundle: Self = serde_json::from_value(value).context("invalid bundle schema")?;
        if bundle.schema_version != 1 {
            bail!("unsupported_bundle_schema:{}", bundle.schema_version);
        }
        if bundle.compatibility_hash != actual_hash {
            bail!(
                "compatibility_hash_mismatch:expected={}:actual={}",
                bundle.compatibility_hash,
                actual_hash
            );
        }
        if bundle.parser.family_id != bundle.family_id
            || bundle.normalization.family_id != bundle.family_id
            || bundle.mapping.family_id != bundle.family_id
        {
            bail!("bundle_family_id_mismatch");
        }
        if bundle.parser.revision != bundle.normalization.parser_revision
            || bundle.parser.revision != bundle.mapping.parser_revision
            || bundle.normalization.revision != bundle.mapping.normalization_revision
        {
            bail!("bundle_revision_mismatch");
        }
        if bundle.mapping.taxonomy != bundle.taxonomy.name {
            bail!("bundle_taxonomy_mismatch");
        }
        Ok(bundle)
    }
}

pub fn calculate_compatibility_hash(value: &Value) -> Result<String> {
    let mut canonical = value.clone();
    let object = canonical
        .as_object_mut()
        .context("bundle root must be an object")?;
    object.remove("compatibility_hash");
    let encoded = serde_json::to_vec(&canonical)?;
    Ok(format!("{:x}", Sha256::digest(encoded)))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Dispatch {
    pub strategy: String,
    pub anchors: Vec<String>,
    pub fallback: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParserFamily {
    pub family_id: String,
    pub revision: u32,
    pub patterns: Vec<GrokPattern>,
    pub layers: Vec<GrokLayer>,
    pub stages: Vec<PipelineStage>,
    pub record_schema: Vec<RecordField>,
    pub constraints: Vec<FieldConstraint>,
    pub evidence: Vec<String>,
    pub family_description: Value,
    pub parser_description: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GrokPattern {
    pub name: String,
    pub definition: String,
    pub description: String,
    pub positive_examples: Vec<String>,
    pub negative_examples: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GrokLayer {
    pub name: String,
    pub source: String,
    pub pattern: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PipelineStage {
    pub name: String,
    pub codec: String,
    pub source: String,
    pub params: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecordField {
    pub name: String,
    pub category: String,
    #[serde(rename = "type")]
    pub field_type: String,
    pub required: bool,
    pub source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FieldConstraint {
    pub field: String,
    pub equals: Option<Value>,
    pub allowed_when: BTreeMap<String, Vec<String>>,
    pub required_when: BTreeMap<String, Vec<String>>,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NormalizationRevision {
    pub family_id: String,
    pub revision: u32,
    pub parser_revision: u32,
    pub fields: Vec<NormalizationField>,
    pub evidence: Vec<String>,
    pub description: String,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NormalizationField {
    pub source: String,
    pub description: String,
    pub operations: Vec<TransformOperation>,
    pub required: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransformOperation {
    pub op: String,
    pub params: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MappingRevision {
    pub family_id: String,
    pub revision: u32,
    pub parser_revision: u32,
    pub normalization_revision: u32,
    pub taxonomy: String,
    pub source_timezone: Option<String>,
    pub mappings: Vec<MappingField>,
    pub evidence: Vec<String>,
    pub description: String,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MappingField {
    pub source: Option<String>,
    pub target: String,
    pub constant: Option<Value>,
    pub value_map: Option<BTreeMap<String, Value>>,
    pub condition: Option<BTreeMap<String, String>>,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Taxonomy {
    pub name: String,
    pub fields: Vec<TaxonomyField>,
    pub values: Vec<TaxonomyValue>,
    pub categories: Vec<TaxonomyCategory>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaxonomyField {
    pub name: String,
    #[serde(rename = "type")]
    pub field_type: String,
    pub required: bool,
    pub automatic: bool,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaxonomyValue {
    pub field: String,
    pub value: String,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaxonomyCategory {
    pub generic: String,
    pub high: String,
    pub low: String,
    pub description: String,
}
