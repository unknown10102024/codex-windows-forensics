# Investigating AI agent file operations: supplementary material

Data and tool for the anonymous submission "Investigating AI Agent File Operations through Windows Artifacts, NTFS Records, and OOXML Toolmarks: A Case Study of OpenAI Codex" (DFC Europe 2027).

## Layout

| Path | Contents |
|---|---|
| `ooxml_attribution/` | Document attribution tool and its rules (`rules_v1.json`) |
| `dataset/documents/creation_modification/` | Document creation and modification experiments: Codex creations (120), Codex modifications (120), Office originals (3), human-modified (3) and human-resaved (7) documents |
| `dataset/documents/tests/` | Test documents: Office-processed (15), new human-created (12), Microsoft 365 blank (3), DOCX from tools outside the derivation data (5), documents from two other PCs (`risotto/`, 60) |
| `dataset/documents/search/` | File search experiment: inputs (15) and modified files (400) |
| `dataset/labels/` | Ground-truth labels (`processing_truth.csv`, `search_truth.csv`, `search_runs.csv`, `search_input_mapping.csv`, `risotto.csv`) |
| `dataset/results/attribution.jsonl` | Attribution output for every OOXML document |
| `dataset/collection/` | KAPE targets and parsing settings |
| `dataset/public_corpora/` | Govdocs1 and NapierOne OOXML files evaluated (list only) |
| `task_prompts/` | Task prompts given to Codex; `prompt_index.csv` links each document to its prompt (`no agent prompt` for human-made or generated inputs) |
| `baseline/` | Baseline rules (B1: Application, creator, lastModifiedBy; B2: ZIP attributes) and their script |

## Document attribution tool

Requires Python 3.12 and lxml 6.0.2.

```sh
python -m pip install -r ooxml_attribution/requirements.txt
python -m ooxml_attribution path/to/document.docx                                  # one file -> JSON
python -m ooxml_attribution path/to/folder --out results.jsonl --csv results.csv  # folder -> JSON Lines and CSV
```

Output fields: `template_lineage`, `last_packager` (all matching paths; `["unknown"]` if no rule matched), and `office_resave`.

## Reproduction

```sh
cd dataset
sha256sum -c SHA256SUMS
python reproduce.py --output /tmp/attribution.jsonl --compare results/attribution.jsonl
cd .. && python baseline/reproduce.py
```

## License

Code: MIT (`LICENSE`). Documents, labels, and results in `dataset/`: CC BY 4.0 (`LICENSE-DATA`), except third-party material embedded in documents.
