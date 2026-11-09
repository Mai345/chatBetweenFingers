"""训练和识别共用的特征规则与 KNN 训练方法。"""

import numpy as np
from sklearn.model_selection import GroupShuffleSplit
from sklearn.metrics import accuracy_score, classification_report
from sklearn.neighbors import KNeighborsClassifier

FEATURE_VERSION = "wrist-palm-63-v1"


def extract_features(landmarks):
    points = np.asarray(
        [[p["x"], p["y"], p["z"]] for p in landmarks], dtype=float
    )
    if points.shape != (21, 3) or not np.isfinite(points).all():
        raise ValueError("需要21个有效关键点")
    palm_size = np.linalg.norm((points[9] - points[0])[:2])
    if palm_size < 1e-6:
        raise ValueError("手掌大小无效，请调整姿势")
    return ((points - points[0]) / palm_size).reshape(63).tolist()


def train_model(samples):
    if not samples:
        raise ValueError("请先采集或导入数据")
    features = np.asarray([s["features"] for s in samples], dtype=float)
    labels = np.asarray([s["label"] for s in samples])
    groups = np.asarray([s["batch_id"] for s in samples])
    classes, counts = np.unique(labels, return_counts=True)
    if len(classes) < 2 or counts.min() < 30:
        raise ValueError(
            "至少需要2种手势，每种至少30个样本，请分多个批次采集"
        )
    evaluation = {
        "available": False,
        "message": "缺少可独立划分的采集批次，未计算独立测试准确率",
    }
    best_k = min(5, len(samples))
    if len(set(groups)) >= 2:
        splitter = GroupShuffleSplit(
            n_splits=30, test_size=0.25, random_state=42
        )
        for train_ids, test_ids in splitter.split(features, labels, groups):
            if set(labels[train_ids]) != set(classes) or set(
                labels[test_ids]
            ) != set(classes):
                continue
            scores = []
            for k in (3, 5):
                if k > len(train_ids):
                    continue
                model = KNeighborsClassifier(n_neighbors=k, weights="distance")
                model.fit(features[train_ids], labels[train_ids])
                predicted = model.predict(features[test_ids])
                scores.append(
                    (accuracy_score(labels[test_ids], predicted), k, predicted)
                )
            accuracy, best_k, predicted = max(scores, key=lambda item: item[0])
            evaluation = {
                "available": True,
                "accuracy": float(accuracy),
                "train_count": len(train_ids),
                "test_count": len(test_ids),
                "report": classification_report(
                    labels[test_ids],
                    predicted,
                    output_dict=True,
                    zero_division=0,
                ),
                "message": "按采集批次分组留出评估；参数选择与评估共用此留出集，仍需另采数据做最终验收",
            }
            break
    model = KNeighborsClassifier(n_neighbors=best_k, weights="distance")
    model.fit(features, labels)
    thresholds = {}
    for label in classes:
        class_features = features[labels == label]
        neighbors = KNeighborsClassifier(n_neighbors=2).fit(
            class_features, np.repeat(label, len(class_features))
        )
        distances = neighbors.kneighbors(class_features)[0][:, 1]
        thresholds[str(label)] = max(
            0.35, float(np.percentile(distances, 95)) * 2
        )
    return {
        "model": model,
        "training_labels": labels.tolist(),
        "feature_version": FEATURE_VERSION,
        "distance_thresholds": thresholds,
        "evaluation": evaluation,
        "sample_count": len(samples),
        "labels": classes.tolist(),
        "k": best_k,
    }


def predict_candidate(bundle, features):
    model = bundle["model"]
    scores = model.predict_proba([features])[0]
    best_index = int(scores.argmax())
    label = str(model.classes_[best_index])
    distances, indices = model.kneighbors([features])
    same_class = [
        float(distance)
        for distance, index in zip(distances[0], indices[0])
        if bundle["training_labels"][index] == label
    ]
    if (
        not same_class
        or min(same_class) > bundle["distance_thresholds"][label]
    ):
        return None
    return {
        "label": label,
        "confidence": float(scores[best_index]),
        "model_version": bundle["version"],
    }
