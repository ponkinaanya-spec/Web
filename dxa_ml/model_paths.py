"""Runtime checkpoint names for the complete research pipeline."""

from pathlib import Path

MODEL_FILENAMES = {
    "anatomy": "anatomy_spine_hip_unknown_efficientnet_b0.pt",
    "artifact": "spine_artifact_densenet121.pt",
    "positioning": "spine_positioning_efficientnet_b0.pt",
    "hip_positioning": "hip_positioning_densenet121_candidate.pt",
    "hip_binary": "hip_binary_candidate.pt",
}
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / "models"


def model_paths(model_dir: str | Path = DEFAULT_MODEL_DIR) -> dict[str, Path]:
    directory = Path(model_dir)
    paths = {task: directory / filename for task, filename in MODEL_FILENAMES.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing model checkpoints: " + ", ".join(missing))
    pointers = [str(path) for path in paths.values() if path.stat().st_size < 1_000_000]
    if pointers:
        raise ValueError("Model files are too small; run 'git lfs pull': " + ", ".join(pointers))
    return paths
