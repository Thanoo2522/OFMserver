from flask import Flask, request, jsonify
import os, json
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, firestore, db as rtdb
from firebase_admin import storage as fb_storage   # 🔹 alias

from google.cloud import storage as gcs_storage    # 🔹 alias

from werkzeug.security import generate_password_hash, check_password_hash

# -------------------------------------
app = Flask(__name__)

# ------------------- Firebase Config -------------------
RTD_URL1 = "https://bestofm-a31a0-default-rtdb.asia-southeast1.firebasedatabase.app/"
BUCKET_NAME = "bestofm-a31a0.firebasestorage.app"

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

# ------------------- Clients -------------------
db = firestore.client()              # ✅ ใช้ได้ ถูกต้อง
rtdb_ref = rtdb.reference("/")

gcs_client = gcs_storage.Client()    # ✅ FIX จุด error
bucket = fb_storage.bucket()         # Firebase Storage bucket

# ------------------------------------------------
def build_prefixes(text):
    text = text.lower().strip()
    prefixes = []
    current = ""
    for ch in text:
        current += ch
        prefixes.append(current)
    return prefixes

# ------------------- check password -------------------
@app.route("/ofm_password", methods=["POST"])
def ofm_password():
    try:
        data = request.get_json()
        nameofm = data.get("nameofm")
        adminpassword = data.get("adminpassword")

        query = (
            db.collection("registeradminOFM")
            .where("nameofm", "==", nameofm)
            .limit(1)
            .stream()
        )

        admin_doc = next(query, None)
        if not admin_doc:
            return jsonify({"status": "not_found"}), 200

        saved_hash = admin_doc.to_dict().get("addminpass")
        if not check_password_hash(saved_hash, adminpassword):
            return jsonify({"status": "wrong_password"}), 200

        return jsonify({"status": "success"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------- register admin -------------------
@app.route("/register_admin_full", methods=["POST"])
def register_admin_full():
    try:
        data = request.get_json()
        nameofm = data.get("nameofm", "").strip()

        if not nameofm:
            return jsonify({"status": "error", "message": "ข้อมูลไม่ครบ"}), 400

        ofm_ref = db.collection("OFM_name").document(nameofm)
        if ofm_ref.get().exists:
            return jsonify({"status": "error", "message": "ชื่อร้านซ้ำ"}), 200

        ofm_ref.set({
            "OFM_name": nameofm,
            "OFM_name_lower": nameofm.lower(),
            "search_prefix": build_prefixes(nameofm),
            "created_at": firestore.SERVER_TIMESTAMP
        })

        # 🔹 สร้าง folder ใน Firebase Storage
        blob = bucket.blob(f"{nameofm}/.keep")
        blob.upload_from_string("")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------- โหลดแผงทั้งหมด -------------------
@app.route("/get_shops", methods=["GET"])
def get_shops():
    ofm = request.args.get("ofm")
    if not ofm:
        return jsonify({"error": "missing ofm"}), 400

    prefix = f"{ofm}/"
    shops = set()

    blobs = gcs_client.list_blobs(bucket.name, prefix=prefix)

    for blob in blobs:
        parts = blob.name.split("/")
        if len(parts) >= 2:
            shops.add(parts[1])

    return jsonify({"shops": sorted(list(shops))})

# ------------------- โหลด mode -------------------
@app.route("/get_modes", methods=["GET"])
def get_modes():
    ofm = request.args.get("ofm")
    shop = request.args.get("shop")

    prefix = f"{ofm}/{shop}/"
    modes = set()

    blobs = gcs_client.list_blobs(bucket.name, prefix=prefix)

    for blob in blobs:
        parts = blob.name.split("/")
        if len(parts) >= 3:
            modes.add(parts[2])

    return jsonify({"modes": sorted(list(modes))})

# ------------------- โหลดรูปแบบ pagination -------------------
@app.route("/get_images", methods=["GET"])
def get_images():
    ofm = request.args.get("ofm")
    shop = request.args.get("shop")
    mode = request.args.get("mode")

    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))

    prefix = f"{ofm}/{shop}/{mode}/"
    blobs = gcs_client.list_blobs(bucket.name, prefix=prefix)

    images = [
        f"https://storage.googleapis.com/{bucket.name}/{b.name}"
        for b in blobs if b.name.lower().endswith(".jpg")
    ]

    start = (page - 1) * page_size
    end = start + page_size

    return jsonify({
        "page": page,
        "total": len(images),
        "has_more": end < len(images),
        "images": images[start:end]
    })

# ----------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
