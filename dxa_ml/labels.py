"""Conservative labels: no invented hip side; missing targets remain NaN."""
import numpy as np
import pandas as pd

TASK_LABELS = {
    "anatomy": ["spine", "hip", "unknown"],
    "spine": ["quality", "positioning", "axis", "artifact"],
    "hip": ["quality", "positioning_rotation", "roi"],
}


def as_bool(series):
    return series.astype(str).str.strip().str.lower().isin(["true", "1", "1.0"])


def assign_targets(manifest):
    frame = manifest.copy()
    for col in ["y_quality", "y_positioning", "y_axis", "y_artifact", "y_positioning_rotation", "y_roi"]:
        frame[col] = np.nan
    frame["label_provenance"] = "unresolved"
    spine = frame.anatomy.eq("spine")
    for label, source in [("quality", "total"), ("positioning", "positioning"),
                          ("axis", "axis"), ("artifact", "artifact")]:
        frame.loc[spine, "y_"+label] = frame.loc[spine, "spine_"+source+"_error"]
    frame.loc[spine, "label_provenance"] = "excel_spine"
    for _, group in frame[frame.anatomy.eq("hip")].groupby("study_id"):
        right = as_bool(group.right_hip_present).iloc[0]
        left = as_bool(group.left_hip_present).iloc[0]
        side = "right" if right and not left else "left" if left and not right else None
        if side and len(group) == 1:
            for label, source in [("quality", "total"), ("positioning_rotation", "positioning"), ("roi", "roi")]:
                frame.loc[group.index, "y_"+label] = group[side+"_hip_"+source+"_error"]
            frame.loc[group.index, "label_provenance"] = "excel_single_side"
        elif right and left and len(group) == 2:
            for label, source in [("quality", "total"), ("positioning_rotation", "positioning"), ("roi", "roi")]:
                r, l = group.iloc[0][["right_hip_"+source+"_error", "left_hip_"+source+"_error"]]
                if pd.notna(r) and pd.notna(l) and r == l:
                    frame.loc[group.index, "y_"+label] = r
            frame.loc[group.index, "label_provenance"] = "excel_bilateral_agreement_per_target"
    ycols = [x for x in frame if x.startswith("y_")]
    if not frame[ycols].stack().isin([0, 1]).all():
        raise ValueError("Labels must be binary or missing")
    return frame


def label_audit(frame):
    report = {}
    for region in ["spine", "hip"]:
        group = frame[frame.anatomy.eq(region)]
        report[region] = {"images": len(group), "studies": group.study_id.nunique(), "targets": {}}
        for label in TASK_LABELS[region]:
            col = group["y_"+label]
            report[region]["targets"][label] = {
                "known": int(col.notna().sum()), "positive": int(col.sum()),
                "unknown": int(col.isna().sum()),
                "development_positive": int(group.loc[group.fold.ne(1), "y_"+label].sum())}
    spine = frame[frame.anatomy.eq("spine")]
    mismatch = spine.y_quality.ne(spine[["y_positioning", "y_axis", "y_artifact"]].max(axis=1))
    report["spine_total_vs_or_excel_rows"] = spine.loc[mismatch, "excel_row"].astype(int).tolist()
    report["hip_side_assumed_from_order"] = False
    return report
