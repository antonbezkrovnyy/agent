use std::fs;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;

use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use log_data_plane::bundle::FamilyBundle;
use log_data_plane::engine::{FamilyEngine, FamilyRegistry};
use log_data_plane::verify_golden;
use serde_json::Value;

#[derive(Debug, Parser)]
#[command(
    name = "log-data-plane",
    version,
    about = "Execute approved log Family Bundles without LLM"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Run {
        #[arg(long = "bundle", required = true)]
        bundles: Vec<PathBuf>,
        #[arg(long, conflicts_with = "input")]
        raw: Option<String>,
        #[arg(long, conflicts_with = "raw")]
        input: Option<PathBuf>,
        #[arg(long)]
        output: Option<PathBuf>,
    },
    Validate {
        #[arg(long = "bundle", required = true)]
        bundles: Vec<PathBuf>,
    },
    Verify {
        #[arg(long)]
        bundle: PathBuf,
        #[arg(long)]
        golden: PathBuf,
    },
    Route {
        #[arg(long = "bundle", required = true)]
        bundles: Vec<PathBuf>,
        #[arg(long)]
        raw: String,
    },
}

fn load_bundles(paths: &[PathBuf]) -> Result<Vec<FamilyBundle>> {
    paths
        .iter()
        .map(|path| FamilyBundle::from_path(path))
        .collect()
}

fn write_line(output: &mut dyn Write, value: &Value) -> Result<()> {
    serde_json::to_writer(&mut *output, value)?;
    output.write_all(b"\n")?;
    Ok(())
}

fn run(
    bundles: Vec<PathBuf>,
    raw: Option<String>,
    input: Option<PathBuf>,
    output: Option<PathBuf>,
) -> Result<()> {
    let registry = FamilyRegistry::new(load_bundles(&bundles)?)?;
    let mut output: Box<dyn Write> = match output {
        Some(path) => Box::new(
            fs::File::create(&path).with_context(|| format!("cannot create {}", path.display()))?,
        ),
        None => Box::new(io::stdout().lock()),
    };
    let mut failed = 0;
    if let Some(raw) = raw {
        let result = registry.process(&raw);
        failed += usize::from(!result.ok);
        write_line(&mut output, &serde_json::to_value(result)?)?;
    } else if let Some(path) = input {
        let file =
            fs::File::open(&path).with_context(|| format!("cannot read {}", path.display()))?;
        for line in io::BufReader::new(file).lines() {
            let raw = line?;
            if raw.is_empty() {
                continue;
            }
            let result = registry.process(&raw);
            failed += usize::from(!result.ok);
            write_line(&mut output, &serde_json::to_value(result)?)?;
        }
    } else {
        bail!("run requires --raw or --input");
    }
    output.flush()?;
    if failed > 0 {
        bail!("processing_failed:{failed}");
    }
    Ok(())
}

fn validate(bundles: Vec<PathBuf>) -> Result<()> {
    let bundles = load_bundles(&bundles)?;
    let mut total = 0;
    let mut failed = 0;
    for bundle in bundles {
        let engine = FamilyEngine::new(bundle)?;
        for raw in &engine.bundle.mapping.evidence {
            total += 1;
            let result = engine.process(raw);
            if !result.ok {
                failed += 1;
                eprintln!("FAILED {}: {:?}", engine.bundle.family_id, result.errors);
            }
        }
    }
    println!(
        "{}",
        serde_json::json!({"ok": failed == 0, "evidence": total, "passed": total - failed, "failed": failed})
    );
    if failed > 0 {
        bail!("validation_failed:{failed}");
    }
    Ok(())
}

fn verify(bundle: PathBuf, golden: PathBuf) -> Result<()> {
    let bundle = FamilyBundle::from_path(&bundle)?;
    let golden: Value = serde_json::from_str(&fs::read_to_string(&golden)?)?;
    let engine = FamilyEngine::new(bundle)?;
    let report = verify_golden(&engine, &golden).map_err(anyhow::Error::msg)?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    if !report.ok {
        bail!("golden_parity_failed");
    }
    Ok(())
}

fn route(bundles: Vec<PathBuf>, raw: String) -> Result<()> {
    let registry = FamilyRegistry::new(load_bundles(&bundles)?)?;
    let candidates = registry.candidates(&raw);
    let result = registry.process(&raw);
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "candidate_count": candidates.len(),
            "status": result.status,
            "candidates": result.candidates,
            "ok": result.ok,
        }))?
    );
    Ok(())
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Command::Run {
            bundles,
            raw,
            input,
            output,
        } => run(bundles, raw, input, output),
        Command::Validate { bundles } => validate(bundles),
        Command::Verify { bundle, golden } => verify(bundle, golden),
        Command::Route { bundles, raw } => route(bundles, raw),
    }
}
