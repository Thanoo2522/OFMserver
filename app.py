from flask import Flask, request, jsonify, send_file
import os, json, base64, traceback, tempfile
from io import BytesIO

import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore

from openai import OpenAI
from PIL import Image

# ------------------- Flask -------------------
app = Flask(__name__)

# ------------------- Firebase Config -------------------
RTD_URL1 = "https://retailstore-4780f-default-rtdb.asia-southeast1.firebasedatabase.app"
BUCKET_NAME = "retailstore-4780f.firebasestorage.app"

service_account_json = os.environ.get("FIREBASE_SERVICE_KEY")
if not service_account_json:
    raise RuntimeError("Missing FIREBASE_SERVICE_KEY")

cred = credentials.Certificate(json.loads(service_account_json))

firebase_admin.initialize_app(
    cred,
    {
        "storageBucket": BUCKET_NAME,
        "databaseURL": RTD_URL1
    }
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
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# --------------------------- View Images ---------------------------
@app.route('/get_view_list', methods=['GET'])
def get_view_list():
    try:
        folder = request.args.get("folder")
        if not folder:
            return jsonify({"error": "Missing ?folder="}), 400

        blobs = bucket.list_blobs(prefix=f"{folder}/")
        filenames = [
            blob.name.replace(f"{folder}/", "")
            for blob in blobs
            if "." in blob.name
        ]

        return jsonify(filenames), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/image_view/<folder>/<filename>', methods=['GET'])
def image_view(folder, filename):
    try:
        blob = bucket.blob(f"{folder}/{filename}")
        if not blob.exists():
            return jsonify({"error": "File not found"}), 404

        temp = tempfile.NamedTemporaryFile(delete=False)
        blob.download_to_filename(temp.name)

        ext = filename.lower().split('.')[-1]
        mimetype = f"image/{'jpeg' if ext == 'jpg' else ext}"

        return send_file(temp.name, mimetype=mimetype)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --------------------------- Upload Image ---------------------------
@app.route("/upload_image_with_folder", methods=["POST"])
def upload_image_with_folder():
    try:
        folder_name = request.form.get("folder_name")
        picturename = request.form.get("picturename")
        file = request.files.get("image_file")

        if not folder_name or not picturename or not file:
            return jsonify({"status": "error", "message": "Missing fields"}), 400

        path = f"{folder_name}/{picturename}"
        blob = bucket.blob(path)

        blob.upload_from_file(file, content_type="image/jpeg")
        blob.make_public()

        return jsonify({
            "status": "success",
            "path": path,
            "public_url": blob.public_url
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# --------------------------- Register / Login ---------------------------
@app.route("/register_shop", methods=["POST"])
def register_shop():
    data = request.get_json()
    shopname = data.get("shopname")
    phone = data.get("phone")
    password = data.get("password")

    if not shopname or not phone or not password:
        return jsonify({"status": "error", "message": "Missing fields"}), 400

    db.collection("Shopname").document(shopname).set({
        "shopname": shopname,
        "phone": phone,
        "password": password
    })

    return jsonify({"status": "success"}), 200


@app.route("/check_password", methods=["POST"])
def check_password():
    data = request.get_json()
    shopname = data.get("shopname")
    input_password = data.get("password")

    if not shopname or not input_password:
        return jsonify({"status": "error", "message": "Missing fields"}), 400

    doc = db.collection("Shopname").document(shopname).get()

    if not doc.exists:
        return jsonify({"status": "not_found"}), 200

    if doc.to_dict().get("password") == input_password:
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "wrong_password"}), 200

#-------------------------------------------
@app.route("/save_product_price", methods=["POST"])
def save_product_price():
    try:
        data = request.get_json()
        print("📦 JSON RECEIVED:", data)

        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON data"
            }), 400

        # ✅ ใช้ตัวใหญ่ให้ตรงกับ MAUI
        shopname = data.get("Shopname")
        textmode = data.get("Textmode")

        if not shopname or not textmode:
            return jsonify({
                "status": "error",
                "message": "Missing Shopname or Textmode"
            }), 400

        num_remainpack = int(data.get("num_remainpack", 0))
        numpack = int(data.get("numpack", 0))
        unitproduct = data.get("unitproduct", "")
        pricepack = float(data.get("pricepack", 0))

        num_remainsingle = int(data.get("num_remainsingle", 0))
        pricesingle = float(data.get("pricesingle", 0))

        db.collection("showname") \
          .document(shopname) \
          .collection("mode") \
          .document(textmode) \
          .set({
              "num_remainpack": num_remainpack,
              "numpack": numpack,
              "unitproduct": unitproduct,
              "pricepack": pricepack,
              "num_remainsingle": num_remainsingle,
              "pricesingle": pricesingle
          })

        return jsonify({
            "status": "success",
            "message": "บันทึกข้อมูลเรียบร้อย"
        }), 200

    except Exception as e:
        print("🔥 ERROR:", e)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

#-----------------------------------------------------
 
