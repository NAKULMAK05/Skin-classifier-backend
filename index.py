from flask import Flask, request, jsonify
from flask_cors import CORS, cross_origin
import matplotlib
matplotlib.use("Agg")  # Use non-interactive backend

import os
import io
import base64
import cv2
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import tensorflow as tf
import joblib

# Initialize the Flask app and set the allowed frontend domain.
app = Flask(__name__)
allowed_origin = "https://skin-lesion-classifier.vercel.app"
CORS(app, resources={r"/*": {"origins": allowed_origin}})

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Load your models (update the paths if needed)
segmentation_model = tf.keras.models.load_model("segmentation_model_final.keras")
classification_model = tf.keras.models.load_model("not_overfitting_final.keras")
nlp_model = joblib.load("path_to_trained_model.pkl")

# Define class labels for the CNN classifier
CLASS_LABELS = ['bkl', 'nv', 'df', 'mel', 'vasc', 'bcc', 'akiec']

@app.after_request
def add_cors_headers(response):
    response.headers.add("Access-Control-Allow-Origin", allowed_origin)
    response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
    response.headers.add("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
    return response

@app.route("/predict", methods=["POST", "OPTIONS"])
@cross_origin(origins=allowed_origin)
def predict():
    # Handle preflight OPTIONS request
    if request.method == "OPTIONS":
        response = app.make_default_options_response()
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
        return response

    try:
        # 1) Ensure an image file was provided
        if "image" not in request.files:
            return jsonify({"error": "No image provided"}), 400

        # 2) Save the uploaded image
        image_file = request.files["image"]
        image_path = os.path.join(UPLOAD_FOLDER, image_file.filename)
        image_file.save(image_path)

        # 3) Retrieve additional metadata from the request
        dx_type = request.form.get("dx_type", "")
        age = request.form.get("age", "")
        sex = request.form.get("sex", "")
        localization = request.form.get("localization", "")

        # 4) Load and preprocess the image
        orig_img = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if orig_img is None:
            raise ValueError("Could not read image from path: " + image_path)
        orig_img = cv2.resize(orig_img, (224, 224))
        orig_img_rgb = cv2.cvtColor(orig_img, cv2.COLOR_BGR2RGB)
        img_norm = orig_img.astype(np.float32) / 255.0
        img_batch = np.expand_dims(img_norm, axis=0)

        # 5) Perform segmentation using the U-Net model
        mask_pred = segmentation_model.predict(img_batch)[0]
        mask_bin = (mask_pred > 0.5).astype(np.float32).squeeze()

        # 6) Create a masked image for CNN classification
        mask_3ch = np.repeat(mask_bin[:, :, np.newaxis], 3, axis=-1)
        masked_img = img_norm * mask_3ch

        # 7) CNN classification on the masked image
        cnn_probs = classification_model.predict(np.expand_dims(masked_img, axis=0))[0]
        top_idx = int(np.argmax(cnn_probs))
        top_class_label = CLASS_LABELS[top_idx] if top_idx < len(CLASS_LABELS) else "Unknown"
        top_class_conf = float(cnn_probs[top_idx])
        all_class_probs_str = ", ".join(
            f"{CLASS_LABELS[i]}: {cnn_probs[i]:.4f}" for i in range(len(CLASS_LABELS))
        )

        # 8) NLP prediction using the additional metadata
        input_df = pd.DataFrame([{
            "dx_type": dx_type,
            "age": age,
            "sex": sex,
            "localization": localization
        }])
        nlp_pred = nlp_model.predict(input_df)[0]

        # 9) Combine predictions if possible (numeric prediction expected)
        try:
            nlp_pred_numeric = float(nlp_pred)
            combined_pred = (cnn_probs + nlp_pred_numeric) / 2.0
            final_idx = int(np.argmax(combined_pred))
            final_label = CLASS_LABELS[final_idx]
        except (ValueError, TypeError):
            final_label = str(nlp_pred)

        # 10) Create a figure for visualization with 2x2 subplots
        fig, axs = plt.subplots(2, 2, figsize=(12, 10))
        axs[0, 0].imshow(masked_img)
        axs[0, 0].axis("off")
        axs[0, 0].set_title("CNN", fontsize=12, pad=10)

        axs[0, 1].imshow(orig_img_rgb)
        axs[0, 1].axis("off")
        axs[0, 1].set_title("Original", fontsize=12, pad=10)

        overlay = orig_img_rgb.copy()
        overlay[mask_bin > 0.5] = [255, 0, 0]
        axs[1, 0].imshow(overlay)
        axs[1, 0].axis("off")
        axs[1, 0].set_title("Segmentation", fontsize=12, pad=10)

        axs[1, 1].imshow(orig_img_rgb)
        axs[1, 1].axis("off")
        axs[1, 1].set_title("Combined", fontsize=12, pad=10)

        plt.tight_layout(pad=2.0)

        # 11) Encode the figure as a base64 string
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close(fig)
        plot_image_base64 = base64.b64encode(buf.getvalue()).decode()

        # 12) Prepare textual outputs
        cnn_output = f"Predicted Class: {top_class_label} (Confidence: {top_class_conf:.2f})"
        nlp_output = f"NLP Prediction: {nlp_pred}"
        segmentation_output = "U-Net Segmentation Applied"
        final_output = f"This disease is most probably classified as: {final_label}"

        return jsonify({
            "plot_image": f"data:image/png;base64,{plot_image_base64}",
            "cnn_output": cnn_output,
            "all_class_probabilities": all_class_probs_str,
            "nlp_output": nlp_output,
            "segmentation_output": segmentation_output,
            "final_output": final_output
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Expose the Flask app as the serverless function handler for Vercel
handler = app
