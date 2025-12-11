from flask import Flask, request, jsonify, send_file
import os
import base64
import traceback
import json
import io
from io import BytesIO
import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore
from openai import OpenAI
import mimetypes
import tempfile

from PIL import Image
 
 

app = Flask(__name__)

# ------------------- Config -------------------
RTD_URL1 = "https://retailstore-4780f-default-rtdb.asia-southeast1.firebasedatabase.app"
BUCKET_NAME = "retailstore-4780f.firebasestorage.app"

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

service_account_json = os.environ.get("FIREBASE_SERVICE_KEY")
cred = credentials.Certificate(json.loads(service_account_json))

firebase_admin.initialize_app(
    cred,
    {"storageBucket": BUCKET_NAME, "databaseURL": RTD_URL1}
)

db = firestore.client()
rtdb_ref = rtdb.reference("/")
bucket = storage.bucket()

# ---------------- OpenAI -------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)


# --------------------------- IMAGE EDIT ---------------------------
 @app.route("/edit_image", methods=["POST"])
def edit_image():
    try:
        if "image" not in request.files:
            return jsonify({"error": "Missing file 'image'"}), 400

        image_file = request.files["image"]
        mime = image_file.mimetype or "image/jpeg"

        edited = client.images.edit(
            model="gpt-image-1",
            image=("image.jpg", image_file.stream, mime),
            prompt=(
                "expand canvas on top and bottom with pure white background, "
                "keep original subject unchanged, clean full white background, "
                "sharpen, enhance clarity, improve lighting"
            ),
            size="1024x1024"
        )

        result_bytes = base64.b64decode(edited.data[0].b64_json)

        return send_file(
            BytesIO(result_bytes),
            mimetype="image/png",
            as_attachment=False
        )

    except Exception as e:
        print("❌ ERROR in /edit_image:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    #---------------------------------------------
@app.route('/image_view/<filename>', methods=['GET'])
def image_view(filename):
    try:
        bucket = storage.bucket()
        blob = bucket.blob(f"modeproduct/{filename}")

        if not blob.exists():
            return jsonify({"error": "File not found"}), 404

        temp_file = tempfile.NamedTemporaryFile(delete=False)
        temp_path = temp_file.name
        temp_file.close()

        blob.download_to_filename(temp_path)

        ext = filename.lower().split('.')[-1]
        mimetype = f"image/{'jpeg' if ext == 'jpg' else ext}"

        return send_file(temp_path, mimetype=mimetype, as_attachment=False)

    except Exception as e:
        return jsonify({"error": str(e)}), 500



# --------------------------- Firebase APIS ---------------------------
@app.route("/upload_image_with_folder", methods=["POST"])
def upload_image_with_folder():
    try:
        folder_name = request.form.get("folder_name")
        file = request.files.get("image_file")

        if not folder_name:
            return jsonify({"status": "error", "message": "folder_name missing"}), 400

        if not file:
            return jsonify({"status": "error", "message": "image_file missing"}), 400

        filename = f"{folder_name}.jpg"
        path = f"{folder_name}/{filename}"

        blob = bucket.blob(path)
        blob.upload_from_file(file, content_type="image/jpeg")
        blob.make_public()

        return jsonify({
            "status": "success",
            "url": blob.public_url,
            "path": path
        })

    except Exception as e:
        print("🔥 ERROR:", e)
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# --------------------------- Login/Register --------------------------
@app.route("/register_shop", methods=["POST"])
def register_shop():
    try:
        data = request.get_json()
        shopname = data.get("shopname")
        phone = data.get("phone")
        password = data.get("password")

        if not shopname or not phone or not password:
            return jsonify({"status": "error", "message": "Missing fields"}), 400

        doc_ref = db.collection("Shopname").document(password)
        doc_ref.set({"shopname": shopname, "phone": phone, "password": password})

        return jsonify({"status": "success", "message": "Saved"}), 200

    except Exception as e:
        print("🔥 ERROR:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/check_password", methods=["POST"])
def check_password():
    try:
        data = request.get_json()
        input_password = data.get("password")

        if not input_password:
            return jsonify({"status": "error", "message": "Missing password"}), 400

        doc_ref = db.collection("Shopname").document(input_password)
        doc = doc_ref.get()

        if doc.exists:
            return jsonify({"status": "success", "message": "Login OK"})
        else:
            return jsonify({"status": "error", "message": "Not registered"})

    except Exception as e:
        print("ERROR:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
