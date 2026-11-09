import csv
from pathlib import Path

import numpy as np
import joblib
from sklearn.model_selection import train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.metrics import accuracy_score, confusion_matrix

DATA_FILE = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "gesture_samples.csv"
)

MODEL_FILE = (
    Path(__file__).resolve().parent.parent
    / "models"
    / "static_knn.joblib"
)

FEATURE_COUNT = 63

feature_names = [
    f"f{index}"
    for index in range(FEATURE_COUNT)
]
features = []
labels = []

with DATA_FILE.open(
    "r",
    newline="",
    encoding="utf-8-sig",
) as csv_file:
    reader = csv.DictReader(csv_file)

    for row in reader:
        sample_features = [
            float(row[feature_name])
            for feature_name in feature_names
        ]

        features.append(sample_features)
        labels.append(row["label"])

X = np.asarray(features, dtype=np.float64)
y = np.asarray(labels)

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42,
    stratify=y,
)

classifier = KNeighborsClassifier(
    n_neighbors=5,
    weights="distance",
)

classifier.fit(X_train, y_train)
y_pred = classifier.predict(X_test)

accuracy = accuracy_score(
    y_test,
    y_pred
)
print(f"准确率：{accuracy * 100:.2f}%")

matrix = confusion_matrix(
    y_test,
    y_pred,
    labels=classifier.classes_,
)

MODEL_FILE.parent.mkdir(
    parents=True,
    exist_ok=True,
)

classifier.fit(X, y)
joblib.dump(classifier, MODEL_FILE)
print(f"模型已保存到：{MODEL_FILE}")
