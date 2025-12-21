import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.ensemble import RandomForestClassifier
from sklearn.utils.class_weight import compute_class_weight
from sklearn.metrics import accuracy_score, classification_report
import joblib

print("Training Traffic Accident Severity Model...")

# 1. Load cleaned dataset
df = pd.read_csv("dataset_traffic_accident_prediction1_clean.csv")

# 2. Remove 'Unknown' severity (optional)
df = df[df["Accident_Severity"] != "Unknown"]

# 3. Features and target
X = df.drop(columns=["Accident_Severity"])
y = df["Accident_Severity"]

# 4. Column types
cat_cols = X.select_dtypes(include=["object"]).columns.tolist()
num_cols = X.select_dtypes(exclude=["object"]).columns.tolist()

print("Categorical columns:", cat_cols)
print("Numeric columns:", num_cols)

# 5. Preprocessor
preprocessor = ColumnTransformer(
    transformers=[
        ("cat", OneHotEncoder(handle_unknown="ignore"), cat_cols),
        ("num", "passthrough", num_cols),
    ]
)

# 6. Class weights
classes = np.unique(y)
class_weights = compute_class_weight("balanced", classes=classes, y=y)
class_weight_dict = dict(zip(classes, class_weights))
print("Class weights:", class_weight_dict)

# 7. Model (Random Forest)
rf_clf = Pipeline(
    steps=[
        ("preprocess", preprocessor),
        ("model", RandomForestClassifier(
            n_estimators=300,
            random_state=42,
            class_weight=class_weight_dict
        ))
    ]
)

# 8. Train / test split
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

# 9. Train and evaluate
rf_clf.fit(X_train, y_train)
y_pred = rf_clf.predict(X_test)

print("Accuracy:", accuracy_score(y_test, y_pred))
print(classification_report(y_test, y_pred))

# 10. Save trained pipeline
joblib.dump(rf_clf, "rf_model.pkl")
print("✅ Saved model as rf_model.pkl")
